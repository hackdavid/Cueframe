import json
import shutil
import subprocess

import pytest

from app.board import checks, layout, mathexpr, timeline, tutor
from app.board.contracts import BoardPlan, Check, Op, Step, problems
from app.handlers.tutorial import Route
from tests.test_tutorial import _auth, _fakes, _wait


def _plan(steps, **kw):
    return BoardPlan(feasible=True, title="t", level="school", problem="p", answer="x=4",
                     steps=steps, **kw)  # fmt: skip


def _check(kind, a, b="", **kw):
    return checks.check(Check(kind=kind, a=a, b=b, **kw), _plan([]))


def test_checks_accept_true_and_reject_false_claims():
    assert _check("equal", "(x+1)**2", "x**2+2*x+1") is None
    assert "≠" in _check("equal", "(x+1)**2", "x**2+1")
    assert _check("equivalent", "2*x+3=11", "2*x=8") is None
    assert _check("equivalent", "2*x=8", "x=4") is None
    assert _check("equivalent", "2*x=8", "x=3") is not None
    assert _check("solves", "x**2-5*x+6=0", values=["2", "3"]) is None
    assert "solutions" in _check("solves", "x**2-5*x+6=0", values=["2"])
    assert _check("derivative", "x**2*sin(x)", "2*x*sin(x)+x**2*cos(x)") is None
    assert _check("antiderivative", "x*exp(x)", "(x-1)*exp(x)") is None
    assert _check("antiderivative", "x*exp(x)", "x*exp(x)") is not None
    assert _check("definite", "x**2", "1/3", lower="0", upper="1") is None
    assert _check("definite", "exp(-x**2)", "sqrt(pi)", lower="-oo", upper="oo") is None
    assert "not" in _check("definite", "x**2", "1/2", lower="0", upper="1")
    assert _check("limit", "sin(x)/x", "1", lower="0") is None


def test_geometry_checks_use_the_drawn_points():
    pts = [Op(kind="figure", x_range=[-1, 5], y_range=[-1, 4]),
           *(Op(kind="point", latex=n, x=x, y=y) for n, x, y in (("A", 0, 0), ("B", 4, 0),
                                                                  ("C", 4, 3)))]  # fmt: skip
    ok = [Check(kind="geometry", a="angle(A,B,C)", b="90"),
          Check(kind="geometry", a="dist(A,C)**2", b="dist(A,B)**2+dist(B,C)**2"),
          Check(kind="geometry", a="area(A,B,C)", b="6")]  # fmt: skip
    plan = _plan([Step(say="Right triangle.", ops=pts, checks=ok)])
    assert checks.run(plan) == (3, [])
    plan.steps[0].checks = [Check(kind="geometry", a="dist(A,C)", b="6")]
    assert "is 5" in checks.run(plan)[1][0]


def test_expressions_are_plain_math_only():
    for bad in ("__import__('os')", "x.func", "lambda: 1", "().__class__"):
        with pytest.raises(mathexpr.MathError):
            mathexpr.expr(bad)
    assert mathexpr.function("x**2")(3) == 9


def test_structural_problems_are_reported_per_op():
    plan = _plan([Step(say="Hi \\frac{1}{2}", ops=[
        Op(kind="plot", expr="x**2"),
        Op(kind="box", target="nope"),
        Op(kind="figure", x_range=[0, 1], y_range=[2, 1]),
        Op(kind="segment", points=["A", "B"]),
    ])])  # fmt: skip
    out = "\n".join(problems(plan))
    for want in ("contains LaTeX", "draw a figure first", "'nope' is not an earlier",
                 "y_range must be", "point 'A' isn't drawn"):  # fmt: skip
        assert want in out


def test_board_grows_forever_and_the_camera_follows_in_16_9():
    b = layout.Board()
    cams = []
    for i in range(60):
        box = b.figure(5, 4) if i % 15 == 3 else b.row(6 if b.beside() else 12, 0.8)
        cam = b.follow(box)
        cams.append(cam)
        lo, hi = cam - layout.FRAME_H / 2, cam + layout.FRAME_H / 2
        assert lo + layout.CAPTION - 1e-9 <= box.bottom and box.top <= hi  # always in view
        assert box.x0 >= layout.LEFT and box.x1 <= layout.RIGHT
    assert layout.TOP - b.bottom() > 6 * layout.FRAME_H  # many screens tall
    assert cams == sorted(cams, reverse=True)  # writing only moves the camera down
    assert b.width() == layout.RIGHT - layout.LEFT


def test_rows_beside_a_figure_are_narrower():
    b = layout.Board()
    fig = b.figure(5, 4)
    assert b.beside() and b.width() == layout.SPLIT - layout.LEFT
    r = b.row(3, 0.8)
    assert r.x1 <= fig.x0 and r.top == fig.top


def _voiced(plan):
    out = []
    for s in plan.steps:
        ws = s.say.split()
        out.append((len(ws) * 0.3, [{"text": w, "t": i * 0.3, "end": i * 0.3 + 0.25}
                                    for i, w in enumerate(ws)]))  # fmt: skip
    return out


SIMPLE = _plan([
    Step(say="Start with two x plus three equals eleven.",
         ops=[Op(kind="write", id="l1", cue="Start", latex="2x + 3 = 11")]),
    Step(say="Take away three on both sides.",
         ops=[Op(kind="write", id="l2", target="l1", cue="three", latex="2x = 8"),
              Op(kind="arrow", target="l1", to="l2", cue="both", text="-3")],
         checks=[Check(kind="equivalent", a="2*x+3=11", b="2*x=8")]),
    Step(say="Halve it, so x is four.",
         ops=[Op(kind="write", id="l3", target="l2", cue="Halve", latex="x = 4"),
              Op(kind="box", target="l3", cue="four", color="green"),
              Op(kind="figure", x_range=[0, 6], y_range=[0, 16], axes=True),
              Op(kind="plot", expr="2*x+3", latex="y=2x+3"),
              Op(kind="area", expr="2*x+3", a=0, b=4),
              Op(kind="riemann", expr="2*x+3", a=0, b=4, n=4)],
         checks=[Check(kind="solves", a="2*x+3=11", values=["4"])]),
])  # fmt: skip


def test_timeline_follows_cue_words_in_whole_frames():
    tl = timeline.build(SIMPLE, _voiced(SIMPLE))
    ev = tl["events"]
    assert ev[0]["frame"] == round((timeline.INTRO + 0.15) * timeline.FPS)  # "Start" is word 1
    assert ev[2]["frame"] >= round((tl["steps"][1]["start"] + 1.2) * timeline.FPS)  # "both"
    for a, b in zip(ev, ev[1:], strict=False):
        assert b["frame"] >= a["frame"] + a["frames"]  # ops never overlap
    assert tl["frames"] == round(tl["seconds"] * timeline.FPS)
    caps = timeline.captions(tl)
    assert caps[0][2] == "Start with two x plus three equals" and caps[1][2] == "eleven."
    assert "00:00:02,000 --> " in timeline.srt(caps)


@pytest.mark.skipif(not shutil.which("latex") or not shutil.which("dvisvgm"),
                    reason="needs LaTeX")  # fmt: skip
def test_engine_renders_1080p_in_parallel_parts(tmp_path):
    from app.board import engine

    (tmp_path / "plan.json").write_text(SIMPLE.model_dump_json())
    tl = timeline.build(SIMPLE, _voiced(SIMPLE))
    (tmp_path / "timeline.json").write_text(json.dumps(tl))
    assert engine.check(tmp_path)["errors"] == []
    res = engine.render(tmp_path, 2)
    assert res["parts"] == 2 and abs(res["seconds"] - tl["seconds"]) < 0.15
    size = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v", "-show_entries",
                           "stream=width,height", "-of", "csv=p=0", res["video"]],
                          capture_output=True, text=True).stdout.strip()  # fmt: skip
    assert size == "1920,1080"


def test_formula_card_sits_right_of_the_working(tmp_path):
    if not shutil.which("latex"):
        pytest.skip("needs LaTeX")
    from app.board.scene import Builder

    plan = _plan([Step(say="Here is the rule.", ops=[
        Op(kind="formula", id="f", text="Quadratic formula",
           latex="x = \\frac{-b \\pm \\sqrt{b^2 - 4ac}}{2a}"),
        Op(kind="write", id="l1", latex="a = 1,\\; b = -5,\\; c = 6"),
        Op(kind="box", target="f"),
    ])])  # fmt: skip
    assert problems(plan) == []
    b = Builder(plan)
    card, row, _ = b.all()[0]
    assert card.focus.x1 == layout.RIGHT and row.focus.x1 <= card.focus.x0
    assert row.focus.top == card.focus.top  # the working starts beside the formula


def test_bad_latex_is_reported_per_op(tmp_path):
    if not shutil.which("latex"):
        pytest.skip("needs LaTeX")
    from app.board import engine

    bad = _plan([Step(say="Oops.", ops=[Op(kind="write", latex="\\frac{1}{2} \\nosuchcmd")])])
    (tmp_path / "plan.json").write_text(bad.model_dump_json())
    err = engine.check(tmp_path)["errors"]
    assert err and err[0]["step"] == 1 and "Undefined control sequence" in err[0]["error"]


class BoardLLM:
    configured = True

    def __init__(self, plans):
        self.plans, self.inputs = list(plans), []

    async def parse(self, schema, **kw):
        self.inputs.append((schema.__name__, kw.get("input", "")))
        if schema is Route:
            return Route(intent="math_tutor", level=None), None
        if schema is BoardPlan:
            return self.plans.pop(0), None
        if schema is tutor.Question:
            return tutor.Question(found=True, question="Solve 2x + 3 = 11"), None
        if schema is tutor.Review:
            return tutor.Review(ok=True, issues=[]), None
        raise AssertionError(schema)


def test_chat_makes_a_checked_board_lesson(client, monkeypatch):
    _fakes(monkeypatch)
    wrong = SIMPLE.model_copy(deep=True)
    wrong.steps[1].checks = [Check(kind="equivalent", a="2*x+3=11", b="2*x=14")]
    llm = BoardLLM([wrong, SIMPLE])
    client.app.state.jobs.llm = llm
    seen = {}

    async def board(ctx, run, p, problem, work, vid=None):
        seen["box"] = run.box
        tl = timeline.build(p, _voiced(p))
        return (
            p,
            tl,
            {"errors": [], "board_height": 9.5, "camera_moves": 3},
            {"seconds": tl["seconds"], "render_seconds": 4.0, "parts": 2},
        )

    def mix(work, tl, n):
        for f in ("tutor.mp4", "thumbnail.png", "captions.srt"):
            (work / f).write_bytes(b"x")
        return work / "tutor.mp4"

    monkeypatch.setattr(tutor, "_board", board)
    monkeypatch.setattr(tutor, "_mix", mix)
    res = client.post("/api/messages", json={"content": "Solve 2x + 3 = 11"},
                      headers=_auth(client)).json()  # fmt: skip
    job = _wait(client, res["job"]["id"], {"waiting_input", "failed"})
    assert job["pending_input"]["options"] == list(tutor.LEVELS)
    ans = {"input_id": job["pending_input"]["input_id"], "value": "High school"}
    client.post(f"/api/jobs/{job['id']}/answer", headers=_auth(client), json=ans)
    job = _wait(client, job["id"], {"succeeded", "failed"})
    assert job["status"] == "succeeded", job["error"]
    # The wrong step went back to the planner with SymPy's finding.
    retry = [i for n, i in llm.inputs if n == "BoardPlan"][1]
    assert "don't have the same solutions" in retry
    assert seen["box"] is not None  # rendered in a (fake) sandbox, not in a browser
    sbs = client.get("/api/sandboxes", headers=_auth(client)).json()
    assert sbs and all(s["status"] == "terminated" for s in sbs)
    msgs = client.get(f"/api/conversations/{res['conversation']['id']}",
                      headers=_auth(client)).json()["messages"]  # fmt: skip
    text = "\n".join(m["content"] for m in msgs)
    assert "1 problem(s), sending it back" in text and "all passed" in text
    assert "<video" in msgs[-1]["content"] and "SymPy checks passed" in msgs[-1]["content"]
    tid = job["result"]["tutorial_id"]
    tut = client.get(f"/api/tutorials/{tid}", headers=_auth(client)).json()
    assert tut["status"] == "ready" and tut["recipe"] == "board"


def test_refused_lesson_explains_why_in_chat(client, monkeypatch):
    _fakes(monkeypatch)
    no = SIMPLE.model_copy(update={"feasible": False, "reason": "1 = 2 is false"})
    client.app.state.jobs.llm = BoardLLM([no])
    body = {"content": "For a high school student: prove that 1 = 2"}
    res = client.post("/api/messages", json=body, headers=_auth(client)).json()
    job = _wait(client, res["job"]["id"], {"waiting_input", "failed", "succeeded"})
    if job["status"] == "waiting_input":
        ans = {"input_id": job["pending_input"]["input_id"], "value": "High school"}
        client.post(f"/api/jobs/{job['id']}/answer", headers=_auth(client), json=ans)
        job = _wait(client, job["id"], {"failed", "succeeded"})
    assert job["status"] == "failed"
    cid = res["conversation"]["id"]
    msgs = client.get(f"/api/conversations/{cid}", headers=_auth(client)).json()["messages"]
    assert "I couldn't make this lesson" in msgs[-1]["content"]
    assert "1 = 2 is false" in msgs[-1]["content"]
    assert client.get("/api/sandboxes", headers=_auth(client)).json() == []


def test_points_are_named_by_id_or_label():
    ops = [Op(kind="figure", x_range=[0, 5], y_range=[-1, 3], axes=True),
           Op(kind="point", id="A", latex="(2,0)", x=2, y=0),
           Op(kind="point", id="B", latex="(3,0)", x=3, y=0),
           Op(kind="segment", points=["A", "B"])]  # fmt: skip
    plan = _plan([Step(say="Roots.", ops=ops, checks=[
        Check(kind="geometry", a="dist(A,B)", b="1"),
        Check(kind="geometry", a="dist(A,C)", b="1")])])  # fmt: skip
    assert problems(plan) == []
    n, bad = checks.run(plan)
    assert n == 2 and len(bad) == 1 and "no drawn point named C" in bad[0]


def test_arrows_only_join_neighbouring_lines():
    rows = [Op(kind="write", id=f"l{i}", latex=f"x = {i}") for i in range(5)]
    plan = _plan([Step(say="Lines.", ops=[*rows, Op(kind="arrow", target="l0", to="l4"),
                                          Op(kind="arrow", target="l3", to="l4")])])  # fmt: skip
    out = problems(plan)
    assert len(out) == 1 and "too far apart" in out[0]


def test_repairs_keep_the_checks_they_dropped():
    fixed = SIMPLE.model_copy(deep=True)
    for s in fixed.steps:
        s.checks = []
    assert tutor.keep_checks(SIMPLE, fixed) == 2
    assert [len(s.checks) for s in fixed.steps] == [len(s.checks) for s in SIMPLE.steps]


def test_point_labels_avoid_axis_numbers():
    if not shutil.which("latex"):
        pytest.skip("needs LaTeX")
    from app.board.scene import Builder, _box, _overlap

    plan = _plan([Step(say="Roots.", ops=[
        Op(kind="figure", x_range=[0, 5], y_range=[-1, 6], axes=True),
        Op(kind="plot", expr="x**2-5*x+6"),
        Op(kind="point", id="A", latex="2", x=2, y=0),
        Op(kind="point", id="B", latex="3", x=3, y=0)])])  # fmt: skip
    b = Builder(plan)
    items = b.all()[0]
    ax = b.fig.ax
    nums = [_box(n) for n in (*ax.x_axis.numbers, *ax.y_axis.numbers)]
    for it in items[2:]:
        assert not any(_overlap(_box(it.mobs[1]), n) for n in nums)


def test_photo_question_is_read_then_taught(client, monkeypatch, settings):
    _fakes(monkeypatch)
    llm = BoardLLM([SIMPLE])
    client.app.state.jobs.llm = llm

    async def board(ctx, run, p, problem, work, vid=None):
        tl = timeline.build(p, _voiced(p))
        return (
            p,
            tl,
            {"errors": [], "board_height": 9.5, "camera_moves": 3},
            {"seconds": tl["seconds"], "render_seconds": 4.0, "parts": 2},
        )

    def mix(work, tl, n):
        for f in ("tutor.mp4", "thumbnail.png", "captions.srt"):
            (work / f).write_bytes(b"x")
        return work / "tutor.mp4"

    monkeypatch.setattr(tutor, "_board", board)
    monkeypatch.setattr(tutor, "_mix", mix)
    up = client.post("/api/uploads", headers=_auth(client),
                     files={"files": ("q.png", b"\x89PNG fake", "image/png")}).json()  # fmt: skip
    body = {"content": "For a high school student: teach me this", "attachment_ids": [up[0]["id"]]}
    res = client.post("/api/messages", json=body, headers=_auth(client)).json()
    job = _wait(client, res["job"]["id"], {"succeeded", "failed", "waiting_input"})
    if job["status"] == "waiting_input":
        ans = {"input_id": job["pending_input"]["input_id"], "value": "High school"}
        client.post(f"/api/jobs/{job['id']}/answer", headers=_auth(client), json=ans)
        job = _wait(client, job["id"], {"succeeded", "failed"})
    assert job["status"] == "succeeded", job["error"]
    assert "Question" in [n for n, _ in llm.inputs]
    plan_input = next(i for n, i in llm.inputs if n == "BoardPlan")
    assert "read from the student's photo: Solve 2x + 3 = 11" in plan_input
    cid = res["conversation"]["id"]
    msgs = client.get(f"/api/conversations/{cid}", headers=_auth(client)).json()["messages"]
    assert any("The question in your photo" in m["content"] for m in msgs)


def test_decimals_are_voiced_digit_by_digit_and_captioned_as_written():
    from app.board import tutor as tt

    spoken, places = tt._spoken("#1 scores 90.20% vs 94.25, up 3 points.")
    assert spoken == "number 1 scores 90 point 2 0 percent vs 94 point 2 5, up 3 points."
    assert places == [2, 2]
    words = [{"text": t, "t": i, "end": i + 0.5} for i, t in enumerate(spoken.split())]
    caps = [w["text"] for w in tt._rejoin(words, places)]
    assert caps == ["number", "1", "scores", "90.20%", "vs", "94.25,", "up", "3", "points."]
    assert tt._rejoin(words, places)[3]["end"] == 7.5


def test_names_and_symbols_are_voiced_plainly():
    from app.board import tutor as tt

    spoken, places = tt._spoken("DeepSeek-V4.1-Flash gives max_tokens ≥ 256K at 1.5x.")
    assert spoken == "DeepSeek-V4.1-Flash gives max tokens at least 256K at 1 point 5x."
    assert places == [1]
