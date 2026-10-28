import asyncio

import httpx
import pytest

from app import heal
from app.handlers.tutorial import resume_interrupted
from app.tutorial import pipeline, planner, qa
from app.tutorial.planner import StepFix
from tests.test_planner import GOOD, StubLLM, _answer, _plan, _send, _setup, _step
from tests.test_tutorial import _auth, _wait


def _msgs(client, cid):
    return client.get(f"/api/conversations/{cid}", headers=_auth(client)).json()["messages"]


def _tut(client, job):
    return client.get(
        f"/api/tutorials/{job['result']['tutorial_id']}", headers=_auth(client)
    ).json()


def test_retry_only_retries_transient_errors():
    heal.BACKOFF = 0
    calls, notes = [], []

    async def flaky():
        calls.append(1)
        if len(calls) < 3:
            req = httpx.Request("POST", "https://x")
            raise httpx.HTTPStatusError(
                "busy", request=req, response=httpx.Response(429, request=req)
            )
        return "ok"

    assert asyncio.run(heal.retry(flaky, "TTS", on_retry=notes.append)) == "ok"
    assert len(calls) == 3 and "retrying (2/4)" in notes[0]

    async def broken():
        raise ValueError("bad input")

    with pytest.raises(ValueError):
        asyncio.run(heal.retry(broken, "TTS"))

    def http(code):
        req = httpx.Request("GET", "https://x")
        return httpx.HTTPStatusError("", request=req, response=httpx.Response(code, request=req))

    assert heal.transient(http(503)) and not heal.transient(http(403))
    assert heal.transient(httpx.ConnectTimeout("slow"))

    class ClientError(Exception):
        def __init__(self, code):
            self.response = {"ResponseMetadata": {"HTTPStatusCode": code}}

    assert heal.transient(ClientError(500)) and not heal.transient(ClientError(403))


def test_unfixable_step_is_replanned_and_recorded(client, monkeypatch):
    fix = StepFix(
        before=[], fixable=False, reason="no such package", step=_step("run", command="x")
    )
    good = _plan([*GOOD[:1], _step("run", command="cowsay hi"), *GOOD[2:]])
    _setup(client, monkeypatch, StubLLM([_plan(GOOD), good], [fix]))
    cid, jid = _send(client)
    _answer(client, jid, pipeline.SHOW)  # same install command: not asked again
    job = _wait(client, jid, {"succeeded", "failed"})
    assert job["status"] == "succeeded", job["error"]
    tut = _tut(client, job)
    assert any("planned the tutorial again" in n for n in tut["spec"]["healing"])
    assert any("planned it again" in m["content"] for m in _msgs(client, cid))
    assert "Fixed automatically" in _msgs(client, cid)[-1]["content"]
    assert len(_sandboxes := client.get("/api/sandboxes", headers=_auth(client)).json()) == 3
    assert all(b["status"] == "terminated" for b in _sandboxes)


def test_step_failing_while_recording_is_fixed_and_recorded_again(client, monkeypatch):
    flaky = _step("run", command="flaky", output_label="Output")
    fix = StepFix(before=[], fixable=True, reason="mirror", step=_step("x", command="cowsay hi"))
    llm = StubLLM([_plan([GOOD[0], flaky, *GOOD[2:]])], [fix])
    _setup(client, monkeypatch, llm, fail=())
    seen = []
    inner = planner.check

    async def check(box, step, rec):
        if step["action"].get("command") == "flaky":
            seen.append(1)
            if len(seen) == 2:  # passes in rehearsal, fails in the first recording take
                rec.ok, rec.returncode, rec.output_tail = False, 100, "E: mirror timeout\n"
        return await inner(box, step, rec)

    monkeypatch.setattr(planner, "check", check)
    cid, jid = _send(client)
    _answer(client, jid, pipeline.SHOW)
    job = _wait(client, jid, {"succeeded", "failed"})
    assert job["status"] == "succeeded", job["error"]
    tut = _tut(client, job)
    run = next(s for s in tut["spec"]["steps"] if s["id"] == "run")
    assert run["action"]["command"] == "cowsay hi"
    assert tut["capture"]["take"] == 2
    assert all(r["ok"] for r in tut["capture"]["records"])
    assert any("failed while recording" in n for n in tut["output"]["healing"])
    assert "E: mirror timeout" in llm.inputs[-1][1]
    boxes = client.get("/api/sandboxes", headers=_auth(client)).json()
    assert len(boxes) == 3 and all(b["status"] == "terminated" for b in boxes)


def test_broken_recording_sandbox_and_failed_quality_check_are_healed(client, monkeypatch):
    _setup(client, monkeypatch, StubLLM([_plan([GOOD[0], _step("run", command="ls"), *GOOD[2:]])]))
    real, takes = pipeline.capture, []

    async def capture(*a, **kw):
        takes.append(1)
        if len(takes) == 1:
            raise ConnectionError("sandbox connection lost")
        stored = client.app.state.store.get_tutorial(a[1])["spec"]["healing"]
        assert any("recording sandbox broke" in n for n in stored)  # saved before it's done
        return await real(*a, **kw)

    checks, forced = [], []

    async def final(*a, **kw):
        checks.append(1)
        return ["the sound track is silent (-91 dB)"] if len(checks) == 1 else []

    inner = pipeline.render

    async def render(tut, work, fetch, save, progress, force=(), on_retry=None):
        forced.append(set(force))
        return await inner(tut, work, fetch, save, progress, force, on_retry)

    monkeypatch.setattr(pipeline, "capture", capture)
    monkeypatch.setattr(qa, "final", final)
    monkeypatch.setattr(pipeline, "render", render)
    _, jid = _send(client)
    _answer(client, jid, pipeline.SHOW)
    job = _wait(client, jid, {"succeeded", "failed"})
    assert job["status"] == "succeeded", job["error"]
    notes = _tut(client, job)["output"]["healing"]
    assert any("recording sandbox broke" in n for n in notes)
    assert any("silent" in n for n in notes)
    assert forced[0] == set() and "title" in forced[1]  # second render redoes every step


def test_quality_failure_that_persists_is_reported_honestly(client, monkeypatch):
    _setup(client, monkeypatch, StubLLM([_plan([GOOD[0], _step("run", command="ls"), *GOOD[2:]])]))

    async def final(*a, **kw):
        return ["the video has no sound track"]

    monkeypatch.setattr(qa, "final", final)
    _, jid = _send(client)
    _answer(client, jid, pipeline.SHOW)
    job = _wait(client, jid, {"succeeded", "failed"})
    assert job["status"] == "failed" and "no sound track" in job["error"]
    assert _tut(client, job)["status"] == "failed"


def test_interrupted_tutorial_resumes_after_restart(client, monkeypatch):
    _setup(client, monkeypatch, StubLLM([_plan([GOOD[0], _step("run", command="ls"), *GOOD[2:]])]))
    cid, jid = _send(client)
    _answer(client, jid, pipeline.SHOW)
    job = _wait(client, jid, {"succeeded", "failed"})
    tid = job["result"]["tutorial_id"]
    store, jobs = client.app.state.store, client.app.state.jobs
    spec = {**store.get_tutorial(tid)["spec"], "healing": ["Upload of x failed; retrying"]}
    store.update_tutorial(tid, status="rendering", active_job="j_dead", spec=spec)
    other = store.create_tutorial(
        store.get_tutorial(tid)["user_id"], cid, "j_old", "planned", "Old", {"steps": []}
    )
    store.update_tutorial(other["id"], status="capturing", active_job="j_old")  # not interrupted

    async def restart():
        return resume_interrupted(jobs, [{"id": "j_dead"}])

    [rid] = client.portal.call(restart)
    res = _wait(client, rid, {"succeeded", "failed"})
    assert res["status"] == "succeeded", res["error"]
    tut = client.get(f"/api/tutorials/{tid}", headers=_auth(client)).json()
    assert tut["version"] == 2 and tut["status"] == "ready"
    notes = tut["output"]["healing"]
    assert notes[0] == "Upload of x failed; retrying" and "server restarted" in notes[1]
    assert tut["spec"]["healing"] == notes
    assert len(client.get("/api/sandboxes", headers=_auth(client)).json()) == 2  # no re-record
    assert store.get_tutorial(other["id"])["status"] == "capturing"
