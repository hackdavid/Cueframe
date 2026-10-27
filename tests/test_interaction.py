"""Real interaction: element-targeted clicks/typing, desktop apps, checks after each step."""

import asyncio
import dataclasses

from app.sandbox.base import MARK, _record, _script
from app.sandbox.contracts import Action, ExecResult
from app.sandbox.fake import FakeSandbox
from app.tutorial import build, pipeline, planner
from tests.test_planner import LONG, StubLLM, _answer, _plan, _sandboxes, _send, _setup, _step
from tests.test_tutorial import _auth, _wait

GUI = [
    _step("install", command="apt-get install -y mousepad", installs=True),
    _step("launch", "launch", command="mousepad", app="mousepad", expect="untitled"),
    _step("write", "type", app="mousepad", text="hello world", expect="hello world"),
    _step("open", "open_url", url="https://www.python.org"),
    _step("download", "click", app="browser", target="Downloads", expect="Download"),
]


def test_scripts_find_elements_by_their_words():
    click = _script(Action(kind="click", target="Sign up now's", app="browser"))
    assert "/opt/cf/ui.py find browser 'Sign up now'\"'\"'s' click" in click
    assert "xdotool click --repeat 1 1" in click and "exit 3" in click
    typed = _script(Action(kind="type", target="Search", text="jq", enter=True, app="browser"))
    assert "find browser Search type" in typed and typed.endswith("xdotool key Return")
    assert _script(Action(kind="type", text="hi")) == "xdotool type --delay 45 -- hi"
    assert _script(Action(kind="launch", command="mousepad")) == "/opt/cf/launch.sh mousepad"
    assert "find mousepad File click" in _script(
        Action(kind="scroll", target="File", app="mousepad")
    )


def test_record_keeps_element_box_and_visible_candidates():
    res = ExecResult(returncode=0, stdout=f"{MARK} 1 2 3 0\n__CFBOX 10 20 30 40\nok")
    rec = _record(0, Action(kind="click", target="Go"), res)
    assert rec.ok and rec.box == [10, 20, 30, 40] and "__CFBOX" not in rec.output_tail
    res = ExecResult(
        returncode=0, stdout=f"{MARK} 1 2 3 3\nNo element matching 'Go'. Visible: a; b"
    )
    bad = _record(0, Action(kind="click", target="Go"), res)
    assert not bad.ok and "Visible: a; b" in bad.error


def test_problems_reject_unsafe_interaction():
    bad = [
        _step("a", "click", app="browser", target="Sign in"),
        _step("b", "type", app="browser", target="Password", text="hunter2"),
        _step("c", "open_url", url="file:///etc/passwd"),
        _step("d", "type", app="browser", target="Key", text="sk-abcdefghijklmnopqrstuv"),
        _step("e", "launch", command="mousepad; rm -rf /"),
        _step("f", "click", app="browser", target=""),
        _step("g", "open_url", url="http://169.254.169.254/latest"),
    ]
    out = " ".join(planner.problems(_plan(bad)))
    for sid in "abcdefg":
        assert f"step {sid}:" in out, sid
    assert planner.problems(_plan(GUI)) == []


def test_actions_and_plan_text_for_gui_steps():
    spec = planner.to_spec(_plan(GUI), "goal")
    acts = {s["id"]: s["action"] for s in spec["steps"]}
    assert acts["launch"] == {"kind": "launch", "command": "mousepad", "app": "mousepad"}
    assert acts["write"]["target"] is None and acts["write"]["app"] == "mousepad"
    assert acts["download"] == {"kind": "click", "target": "Downloads", "app": "browser"}
    text = planner.describe_plan(spec)
    assert "click “Downloads”" in text and "type `hello world` in mousepad" in text


def test_check_waits_for_expected_text_and_reports_what_is_visible(monkeypatch):
    real = asyncio.sleep
    monkeypatch.setattr(planner.asyncio, "sleep", lambda s: real(0))
    box = FakeSandbox()
    step = {"action": {"kind": "click", "target": "Go", "app": "browser"}, "expect": "Docs"}
    ok = _record(0, Action(kind="click"), ExecResult(returncode=0, stdout=f"{MARK} 1 2 3 0"))
    box.screen = {"browser": "https://x/\nPython Docs"}
    assert asyncio.run(planner.check(box, step, ok)).ok
    box.screen = {"browser": "https://x/\nHome\nAbout"}
    rec = asyncio.run(planner.check(box, step, ok))
    assert not rec.ok and "Home | About" in rec.error


def test_element_steps_zoom_to_the_element():
    step = {"id": "go", "label": "Go", "action": {"kind": "click", "target": "Go",
            "app": "browser"}, "highlight": {"command": "Download"}}  # fmt: skip
    rec = {"started": 0, "ended": 1, "t_in": 0, "t_out": 5, "ok": True, "returncode": 0,
           "box": [900, 500, 120, 40]}  # fmt: skip
    st = build.plan_step(step, rec, [], "terminal")
    assert st.app == "browser" and st.camera[1].cx == 960 and st.camera[1].cy == 520
    hl = st.highlights[0]
    assert (hl.x, hl.y, hl.w, hl.label) == (894, 494, 132, "Download")
    assert {s.kind for s in st.sfx} == {"whoosh", "click"}


def test_gui_tutorial_repairs_a_missing_element_from_what_is_visible(client, monkeypatch):
    monkeypatch.setattr(FakeSandbox, "screen", {"mousepad": "untitled\nhello world",
                                                "browser": "Download Python"})  # fmt: skip
    monkeypatch.setattr(FakeSandbox, "missing", {"Downloads"})
    fix = planner.StepFix(fixable=True, reason="label", step=_step(
        "x", "click", app="browser", target="Download", expect="Download"))  # fmt: skip
    llm = StubLLM([_plan(GUI, intro=LONG)], [fix])
    _setup(client, monkeypatch, llm, fail=())
    _, jid = _send(client, "Make a tutorial: write a note in mousepad")
    job = _answer(client, jid, pipeline.SHOW)
    assert "mousepad" in job["pending_input"]["question"]
    job = _wait(client, jid, {"succeeded", "failed"})
    assert job["status"] == "succeeded", job["error"]
    assert "Visible: download" in llm.inputs[-1][1]  # the fix sees the real candidates
    tut = client.get(f"/api/tutorials/{job['result']['tutorial_id']}", headers=_auth(client)).json()
    states = {s["id"]: s for s in tut["graph"]["states"]}
    assert states["launch"]["app"] == "mousepad" and states["download"]["app"] == "browser"
    assert states["download"]["highlights"]  # zoomed onto the found element
    assert all(b["status"] == "terminated" for b in _sandboxes(client))


def test_sandbox_limits_stop_new_sandboxes_before_any_cost(client, monkeypatch):
    from app.sandbox.manager import SandboxLimit

    mgr, store = client.app.state.sandboxes, client.app.state.store
    _, jid = _send(client, "hello")
    cid = store.get_job(jid)["conversation_id"]
    base = mgr.settings
    monkeypatch.setattr(mgr, "settings", dataclasses.replace(base, user_daily_sandbox_minutes=1))
    sb = store.create_sandbox(jid, cid, "fake")
    store.update_sandbox(sb["id"], status="terminated", seconds=61)
    try:
        mgr.check_limits(cid)
        raise AssertionError("limit not enforced")
    except SandboxLimit as exc:
        assert "Daily sandbox limit" in str(exc)
    monkeypatch.setattr(mgr, "settings", dataclasses.replace(base, user_max_sandboxes=1))
    store.update_sandbox(store.create_sandbox(jid, cid, "fake")["id"], status="running")
    try:
        mgr.check_limits(cid)
        raise AssertionError("limit not enforced")
    except SandboxLimit as exc:
        assert "limit 1" in str(exc)


def test_live_view_is_behind_login_and_owner_only(client, anon):
    _, jid = _send(client, "hello")
    assert anon.get(f"/api/jobs/{jid}/live/vnc.html").status_code == 401
    r = client.get(f"/api/jobs/{jid}/live/vnc.html", headers=_auth(client))
    assert r.status_code == 409  # owner, but no sandbox running
    assert pipeline.live_path(jid).startswith(f"/api/jobs/{jid}/live/vnc.html?")
    assert f"path=api/jobs/{jid}/live/websockify" in pipeline.live_path(jid)
