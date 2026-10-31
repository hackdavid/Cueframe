"""Real interaction: element-targeted clicks/typing, desktop apps, checks after each step."""

import asyncio
import dataclasses

from app.sandbox.base import MARK, _record, _script
from app.sandbox.contracts import Action, ExecResult
from app.sandbox.fake import FakeSandbox
from app.tutorial import build, pipeline, planner
from tests.test_planner import (
    LONG,
    StubLLM,
    _answer,
    _plan,
    _sandboxes,
    _send,
    _setup,
    _step,
    fixes,
)
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
    assert "find browser Search type" in typed and "alt+o" in typed
    assert "xdotool key Return;; esac" in typed
    assert _script(Action(kind="type", text="hi")) == "xdotool type --delay 45 -- hi"
    field = _script(Action(kind="type", target="W:", text="900", clear=True, app="browser"))
    assert "xdotool click 1; sleep 0.2; xdotool key End shift+Home" in field
    assert "End shift+Home" not in typed
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
    fix = planner.StepFix(drop=False, before=[], fixable=True, reason="label", step=_step(
        "x", "click", app="browser", target="Download", expect="Download"))  # fmt: skip
    llm = StubLLM([_plan(GUI, intro=LONG)], [fix])
    _setup(client, monkeypatch, llm, fail=())
    _, jid = _send(client, "Make a tutorial: write a note in mousepad")
    job = _answer(client, jid, pipeline.SHOW)
    assert "mousepad" in job["pending_input"]["question"]
    job = _wait(client, jid, {"succeeded", "failed"})
    assert job["status"] == "succeeded", job["error"]
    assert "Visible: download" in fixes(llm)[-1]  # the fix sees the real candidates
    tut = client.get(f"/api/tutorials/{job['result']['tutorial_id']}", headers=_auth(client)).json()
    states = {s["id"]: s for s in tut["graph"]["states"]}
    assert states["launch"]["app"] == "mousepad" and states["download"]["app"] == "browser"
    assert states["download"]["highlights"]  # zoomed onto the found element
    boxes = _sandboxes(client)
    assert len(boxes) == 1 and boxes[0]["status"] == "terminated"  # fixed mid-take
    cap = tut["capture"]
    assert cap["take"] == 1 and cap["rehearsal"]["kept_repairs"] == 1
    tries = [r for r in cap["records"] if r["step"] == "download"]
    assert [(r["ok"], r["in_video"]) for r in tries] == [(False, False), (True, True)]
    assert "kept recording the take" in str(client.app.state.store.get_job(jid))


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


def test_drag_menu_and_canvas_points():
    drag = _script(Action(kind="drag", target="canvas", at=[0.1, 0.5], to=[0.9, 0.5]))
    assert "find browser canvas click" in drag and "xdotool mousedown 1" in drag
    assert "$4 * 100 / 1000" in drag and "$4 * 900 / 1000" in drag and "mouseup 1" in drag
    menu = _script(Action(kind="menu", target="Layer > Layer Style>Drop Shadow", app="browser"))
    assert [
        p in menu
        for p in (
            "find browser Layer click",
            "find browser 'Layer Style' click",
            "find browser 'Drop Shadow' click",
        )
    ] == [True] * 3
    click = _script(Action(kind="click", target="canvas", at=[0.25, 1.4]))
    assert "$4 * 250 / 1000" in click and "$5 * 1000 / 1000" in click
    res = ExecResult(returncode=0, stdout=f"{MARK} 1 2 3 0\n__CFBOX 1 1 9 9\n__CFBOX 5 6 7 8"
                     "\n__CFDRAG 10 20 300 20\nok")  # fmt: skip
    rec = _record(0, Action(kind="drag", target="canvas"), res)
    assert rec.box == [5, 6, 7, 8] and rec.path == [10, 20, 300, 20] and rec.output_tail == "ok"


def test_problems_check_menu_and_drag_steps():
    bad = [
        _step("a", "menu", app="browser", target="Filter"),
        _step("b", "drag", app="browser", target="canvas", at=[0.1, 0.1]),
        _step("c", "click", app="browser", target="canvas", at=[2, 0.5]),
        _step("d", "menu", app="browser", target="File > Sign in"),
    ]
    out = " ".join(planner.problems(_plan(bad)))
    for sid in "abcd":
        assert f"step {sid}:" in out, sid
    ok = [
        _step("m", "menu", app="browser", target="Select → Remove BG", expect="Remove BG"),
        _step("g", "drag", app="browser", target="canvas", at=[0.1, 0.5], to=[0.9, 0.5]),
        *GUI,
    ]
    assert planner.problems(_plan(ok)) == []
    spec = planner.to_spec(_plan(ok), "goal")
    assert spec["steps"][0]["action"]["target"] == "Select > Remove BG"
    assert spec["steps"][1]["action"]["to"] == [0.9, 0.5]


def test_write_file_and_attached_files_reach_the_sandbox(tmp_path):
    box = FakeSandbox()
    data = bytes(range(256)) * 400
    asyncio.run(box.write_file("/work/p.jpg", data))
    chunks = [s for s in box.scripts if s.startswith("printf %s ")]
    assert len(chunks) == 3 and box.scripts[-1].startswith("base64 -d /work/p.jpg.b64")
    f = tmp_path / "me.png"
    f.write_bytes(b"png")
    box = FakeSandbox()
    asyncio.run(planner.put_files(box, {"files": [{"name": "me.png", "path": str(f)}]}))
    assert any("/work/me.png" in s for s in box.scripts)
    assert "use them: /work/me.png (image/png)" in planner.with_files(
        "edit it", [{"name": "me.png", "content_type": "image/png"}]
    )


def test_typing_into_a_named_field_replaces_its_value():
    from app.tutorial.planner import action

    named = action(_step("w", "type", app="browser", target="W:", text="900"))
    assert named["clear"] is True and named["target"] == "W:"
    assert action(_step("t", "type", app="browser", text="Studio Portrait"))["clear"] is False


def test_plan_summary_names_menu_and_drag_steps():
    menu = {"kind": "menu", "target": "Select > Magic Cut...", "app": "browser"}
    drag = {"kind": "drag", "target": "canvas", "app": "browser"}
    spec = {"steps": [{"label": "Cut out", "action": menu}, {"label": "Paint", "action": drag}]}
    text = planner.describe_plan(spec)
    assert "**Select > Magic Cut...**" in text and "drag across “canvas”" in text


def test_repair_can_add_the_missing_step_before_the_failed_one(client, monkeypatch):
    monkeypatch.setattr(FakeSandbox, "screen", {"mousepad": "untitled\nhello world",
                                                "browser": "Download Python"})  # fmt: skip
    monkeypatch.setattr(FakeSandbox, "missing", {"Downloads"})
    start = _step("p", "click", app="browser", target="Start using it")
    fix = planner.StepFix(drop=False, fixable=True, reason="start screen", before=[start],
                          step=_step("x", "click", app="browser", target="Download",
                                     expect="Download"))  # fmt: skip
    llm = StubLLM([_plan(GUI, intro=LONG)], [fix])
    _setup(client, monkeypatch, llm, fail=())
    _, jid = _send(client, "Make a tutorial: write a note in mousepad")
    _answer(client, jid, pipeline.SHOW)
    job = _wait(client, jid, {"succeeded", "failed"})
    assert job["status"] == "succeeded", job["error"]
    tut = client.get(f"/api/tutorials/{job['result']['tutorial_id']}", headers=_auth(client)).json()
    ids = [s["id"] for s in tut["graph"]["states"]]
    assert ids.index("download_pre1") + 1 == ids.index("download")
    assert "`Start using it` → `Download`" in str(client.app.state.store.get_job(jid))


def test_a_repair_that_repeats_the_failed_step_is_asked_again(client, monkeypatch):
    monkeypatch.setattr(FakeSandbox, "screen", {"mousepad": "untitled\nhello world",
                                                "browser": "Download Python"})  # fmt: skip
    monkeypatch.setattr(FakeSandbox, "missing", {"Downloads"})
    same = planner.StepFix(
        drop=False, before=[], fixable=True, reason="retry", step=GUI[-1].model_copy()
    )
    fix = planner.StepFix(drop=False, before=[], fixable=True, reason="label", step=_step(
        "x", "click", app="browser", target="Download", expect="Download"))  # fmt: skip
    llm = StubLLM([_plan(GUI, intro=LONG)], [same, fix])
    _setup(client, monkeypatch, llm, fail=())
    _, jid = _send(client, "Make a tutorial: write a note in mousepad")
    _answer(client, jid, pipeline.SHOW)
    job = _wait(client, jid, {"succeeded", "failed"})
    assert job["status"] == "succeeded", job["error"]
    asks = [x for name, x in llm.inputs if name == "StepFix"]
    assert len(asks) == 2 and "Your fix was the same step" in asks[1]


def test_a_repair_aimed_at_something_not_on_screen_is_asked_again():
    seen = ["learn photo editing", "start using photopea"]
    menu = _step("m", "menu", app="browser", target="File > Open...")
    assert planner.off_screen(menu, seen) == "file"
    start = _step("s", "click", app="browser", target="Start using Photopea")
    assert planner.off_screen(start, seen) is None
    assert planner.off_screen(_step("t", "terminal", command="ls"), seen) is None


def test_busy_sandbox_keeps_the_provider_idle_timer_alive():
    import asyncio

    from app.sandbox.manager import SandboxManager

    calls = []

    class Box:
        async def beat(self):
            calls.append("beat")

    async def run():
        mgr = SandboxManager.__new__(SandboxManager)
        mgr._live = {"sb": Box()}
        task = asyncio.create_task(mgr._heartbeat("sb", every=0.01))
        await asyncio.sleep(0.05)
        task.cancel()

    asyncio.run(run())
    assert calls and set(calls) == {"beat"}


def test_a_repair_can_drop_a_step_that_isnt_needed(client, monkeypatch):
    monkeypatch.setattr(FakeSandbox, "screen", {"mousepad": "untitled\nhello world",
                                                "browser": "Download Python"})  # fmt: skip
    monkeypatch.setattr(FakeSandbox, "missing", {"Downloads"})
    fix = planner.StepFix(drop=True, before=[], fixable=True, reason="applied already",
                          step=GUI[-1].model_copy())  # fmt: skip
    llm = StubLLM([_plan(GUI, intro=LONG)], [fix])
    _setup(client, monkeypatch, llm, fail=())
    _, jid = _send(client, "Make a tutorial: write a note in mousepad")
    _answer(client, jid, pipeline.SHOW)
    job = _wait(client, jid, {"succeeded", "failed"})
    assert job["status"] == "succeeded", job["error"]
    tut = client.get(f"/api/tutorials/{job['result']['tutorial_id']}", headers=_auth(client)).json()
    assert "download" not in [s["id"] for s in tut["graph"]["states"]]
    assert "step was dropped" in str(client.app.state.store.get_job(jid))


def test_browser_steps_wait_for_the_page_and_close_consent_banners():
    from app.sandbox.base import BROWSER_READY

    opened = _script(Action(kind="open_url", url="https://example.com"))
    assert opened.endswith(BROWSER_READY) and "ui.py settle browser" in BROWSER_READY
    click = _script(Action(kind="click", target="Go", app="browser"))
    assert click.startswith(BROWSER_READY) and "ui.py popup browser" in click
    assert not _script(Action(kind="click", target="7", app="galculator")).startswith(BROWSER_READY)
    out = f"{MARK} 1 2 3 0\n__CFPOP 10 20 30 40 reject all\n__CFBOX 1 2 3 4\nok"
    rec = _record(0, Action(kind="click", target="Go"), ExecResult(returncode=0, stdout=out))
    assert rec.popups == ["reject all"] and rec.box == [1, 2, 3, 4]
    assert "__CFPOP" not in rec.output_tail


def test_plans_never_click_consent_banners():
    plan = _plan([_step("ok", "click", app="browser", target="Do not consent", expect="x")] * 3)
    assert any("consent" in p for p in planner.problems(plan))
