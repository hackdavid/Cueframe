import time

from app.sandbox.base import MARK, _record, _script
from app.sandbox.contracts import Action, ExecResult


def _auth(c):
    return {"Authorization": f"Bearer {c.token}"}


def _wait(c, jid, statuses, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        job = c.get(f"/api/jobs/{jid}", headers=_auth(c)).json()
        if job["status"] in statuses:
            return job
        time.sleep(0.05)
    raise AssertionError(f"job stuck in {job['status']}")


def test_sandbox_run_records_actions_and_terminates(client):
    body = {"content": "show https://example.com", "job_kind": "sandbox.run"}
    jid = client.post("/api/messages", json=body, headers=_auth(client)).json()["job"]["id"]
    job = _wait(client, jid, {"succeeded", "failed"})
    assert job["status"] == "succeeded", job["error"]
    acts = job["result"]["actions"]
    assert [a["action"]["kind"] for a in acts] == ["open_url", "wait"]
    assert all(a["t_out"] >= a["t_in"] for a in acts)
    assert job["result"]["recording"]["url"].endswith("recording.mp4")
    sb = client.get("/api/sandboxes", headers=_auth(client)).json()
    assert sb[0]["status"] == "terminated" and sb[0]["reason"] == "job finished"


def test_terminal_step_asks_user_first_and_hides_when_installed(client):
    spec = {"actions": [{"kind": "terminal", "command": "apt-get install -y cowsay"},
                        {"kind": "open_url", "url": "https://example.com"}]}  # fmt: skip
    body = {"content": "go", "job_kind": "sandbox.run", "job_input": spec}
    jid = client.post("/api/messages", json=body, headers=_auth(client)).json()["job"]["id"]
    job = _wait(client, jid, {"waiting_input"})
    assert client.get("/api/sandboxes", headers=_auth(client)).json() == []  # not started yet
    pending = job["pending_input"]
    bad = client.post(f"/api/jobs/{jid}/answer", headers=_auth(client),
                      json={"input_id": pending["input_id"], "value": "nope"})  # fmt: skip
    assert bad.status_code == 422
    answer = {"input_id": pending["input_id"], "value": pending["options"][0]}
    ok = client.post(f"/api/jobs/{jid}/answer", headers=_auth(client), json=answer)
    assert ok.status_code == 200
    job = _wait(client, jid, {"succeeded", "failed"})
    assert [a["in_video"] for a in job["result"]["actions"]] == [False, True]


def test_failed_step_marks_job_failed_and_still_terminates(client, monkeypatch):
    provider = client.app.state.sandboxes.provider
    monkeypatch.setattr(provider, "create", classmethod(lambda cls, s, u="": _failing()))
    body = {"content": "https://example.com", "job_kind": "sandbox.run"}
    jid = client.post("/api/messages", json=body, headers=_auth(client)).json()["job"]["id"]
    job = _wait(client, jid, {"succeeded", "failed"})
    assert job["status"] == "failed" and "Step 1 failed" in job["error"]
    assert job["result"]["actions"][0]["ok"] is False and job["result"]["recording"]["url"]
    assert (
        client.get("/media/r2/cueframe/jobs/j_nope/x.png", headers=_auth(client)).status_code == 404
    )
    sb = client.get("/api/sandboxes", headers=_auth(client)).json()[0]
    assert sb["status"] == "terminated" and sb["reason"] == "job failed"


async def _failing():
    from app.sandbox.fake import FakeSandbox

    return FakeSandbox(fail_on={0})


def test_record_parsing_and_scripts():
    res = ExecResult(returncode=0, stdout=f"{MARK} 10.0 11.0 11.5 0\nhello")
    rec = _record(0, Action(kind="key", keys="ctrl+s"), res)
    assert rec.ok and rec.settled == 11.5 and rec.output_tail == "hello"
    res = ExecResult(returncode=0, stdout=f"{MARK} 1 2 3 7")
    bad = _record(1, Action(kind="key", keys="x"), res)
    assert not bad.ok and bad.returncode == 7
    assert "xdotool type --delay 45 -- 'a b'" in _script(Action(kind="type", text="a b"))
    assert "--repeat 2 1" in _script(Action(kind="double_click", x=5, y=6))


def test_waiting_jobs_do_not_hold_worker_slots(client):
    spec = {"actions": [{"kind": "terminal", "command": "true"}]}
    for _ in range(2):  # max_concurrent_jobs=2 in tests
        body = {"content": "go", "job_kind": "sandbox.run", "job_input": spec}
        jid = client.post("/api/messages", json=body, headers=_auth(client)).json()["job"]["id"]
        _wait(client, jid, {"waiting_input"})
    demo = client.post("/api/messages", json={"content": "hi"}, headers=_auth(client)).json()
    assert _wait(client, demo["job"]["id"], {"succeeded", "failed"})["status"] == "succeeded"


def test_cancelled_waiting_job_does_not_free_extra_slot(client):
    spec = {"actions": [{"kind": "terminal", "command": "true"}]}
    body = {"content": "go", "job_kind": "sandbox.run", "job_input": spec}
    waiting = [client.post("/api/messages", json=body, headers=_auth(client)).json()["job"]["id"]
               for _ in range(2)]  # fmt: skip
    for jid in waiting:
        _wait(client, jid, {"waiting_input"})
    client.post(f"/api/jobs/{waiting[0]}/cancel", headers=_auth(client))
    _wait(client, waiting[0], {"cancelled"})
    mgr = client.app.state.jobs
    assert mgr._slots._value == 2  # nobody running, both permits free


def test_r2_media_bearer_and_ownership(client):
    body = {"content": "go", "job_kind": "sandbox.run", "job_input": {"actions": [
        {"kind": "open_url", "url": "https://example.com"}]}}  # fmt: skip
    jid = client.post("/api/messages", json=body, headers=_auth(client)).json()["job"]["id"]
    _wait(client, jid, {"succeeded"})
    client.app.state.storage.presign = lambda key, seconds=3600: f"https://r2.test/{key}"
    client.cookies.clear()
    own = client.get(f"/media/r2/cueframe/jobs/{jid}/recording.mp4", headers=_auth(client),
                      follow_redirects=False)  # fmt: skip
    assert own.status_code == 307 and own.headers["location"].endswith("recording.mp4")
    for bad in (f"other/jobs/{jid}/recording.mp4", f"cueframe/jobs/{jid}/../j_x/recording.mp4"):
        assert client.get(f"/media/r2/{bad}", headers=_auth(client)).status_code == 404


def test_provider_released_when_last_sandbox_stops(client, monkeypatch):
    provider = client.app.state.sandboxes.provider
    calls: list[int] = []

    async def release(cls, settings):  # type: ignore[no-untyped-def]
        calls.append(len(client.app.state.sandboxes._live))

    monkeypatch.setattr(provider, "release", classmethod(release))
    body = {"content": "https://example.com", "job_kind": "sandbox.run"}
    jid = client.post("/api/messages", json=body, headers=_auth(client)).json()["job"]["id"]
    _wait(client, jid, {"succeeded", "failed"})
    assert calls and calls[-1] == 0


def test_release_skipped_while_another_worker_has_a_sandbox(client, monkeypatch):
    mgr = client.app.state.sandboxes
    calls: list[int] = []

    async def release(cls, settings):  # type: ignore[no-untyped-def]
        calls.append(1)

    monkeypatch.setattr(mgr.provider, "release", classmethod(release))
    mgr.store.create_sandbox(None, None, "fake")  # row owned by "another worker"
    import asyncio

    asyncio.run(mgr._release_if_idle())
    assert calls == []
