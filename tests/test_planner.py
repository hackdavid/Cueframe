from app.handlers.tutorial import Route
from app.tutorial import build, pipeline, planner
from app.tutorial.build import caption_words
from app.tutorial.contracts import Word
from app.tutorial.planner import PlannedStep, StepFix, TutorialPlan
from tests.test_tutorial import _auth, _fakes, _wait


def _step(id_, kind="terminal", **kw):
    base = dict(id=id_, label=id_.title(), kind=kind, command="", url="", keys="",
                installs=False, expect="", narration=f"Now {id_}.", after="", badge="",
                highlight="", output_label="", success=False, app="", target="",
                text="", enter=False, at=[], to=[])  # fmt: skip
    return PlannedStep(**{**base, **kw})


LONG = " ".join(["word"] * 120)


def _plan(steps, feasible=True, reason="", intro=LONG):
    return TutorialPlan(feasible=feasible, reason=reason, title="Say hi with cowsay",
                        subtitle="in a minute", summary="Install cowsay.", software="cowsay",
                        tags=["linux"], done="Cowsay says hi", intro=intro, outro="Bye.",
                        say=[{"written": "nginx", "spoken": "engine-x"}], steps=steps)  # fmt: skip


GOOD = [
    _step("install", command="apt-get install -y cowsay", installs=True, after="Done."),
    _step("run", command="badcmd", output_label="Output"),
    _step("open", "open_url", url="localhost"),
    _step("reload", "key", keys="F5"),
]


class StubLLM:
    configured = True

    def __init__(self, plans, fixes=()):
        self.plans, self.fixes, self.inputs = list(plans), list(fixes), []

    async def parse(self, schema, **kw):
        self.inputs.append((schema.__name__, kw.get("input", "")))
        if schema is Route:
            return Route(intent="make_tutorial"), None
        if schema is TutorialPlan:
            return self.plans.pop(0), None
        if schema is StepFix:
            return self.fixes.pop(0), None
        raise AssertionError(schema)


ERR = "user@cueframe:/work$ badcmd\nbash: badcmd: command not found\n"


def _setup(client, monkeypatch, llm, fail=("badcmd",)):
    _fakes(monkeypatch)
    client.app.state.jobs.llm = llm

    async def research(ctx, goal):
        return ""

    real = planner.check

    async def check(box, step, rec):
        if step["action"].get("command") in fail:
            rec.ok, rec.returncode, rec.output_tail = False, 127, ERR
        return await real(box, step, rec)

    monkeypatch.setattr(planner, "research", research)
    monkeypatch.setattr(planner, "check", check)


def _send(client, text="Make a tutorial: say hi with cowsay"):
    res = client.post("/api/messages", json={"content": text}, headers=_auth(client)).json()
    return res["conversation"]["id"], res["job"]["id"]


def _answer(client, jid, value):
    job = _wait(client, jid, {"waiting_input", "failed", "succeeded"})
    assert job["status"] == "waiting_input", job["error"]
    assert client.get("/api/sandboxes", headers=_auth(client)).json() == []  # nothing paid yet
    client.post(f"/api/jobs/{jid}/answer", headers=_auth(client),
                json={"input_id": job["pending_input"]["input_id"], "value": value})  # fmt: skip
    return job


def _sandboxes(client):
    return client.get("/api/sandboxes", headers=_auth(client)).json()


def test_planned_tutorial_rehearses_repairs_then_records(client, monkeypatch):
    bad = _plan([*GOOD[:1], _step("edit", command="vim index.html")])
    fix = StepFix(before=[], fixable=True, reason="typo", step=_step("x", command="cowsay hi",
                  expect="OK", output_label="Output"))  # fmt: skip
    llm = StubLLM([bad, _plan(GOOD)], [fix])
    _setup(client, monkeypatch, llm)
    cid, jid = _send(client)
    job = _answer(client, jid, pipeline.SHOW)
    assert "apt-get install -y cowsay" in job["pending_input"]["question"]
    job = _wait(client, jid, {"succeeded", "failed"})
    assert job["status"] == "succeeded", job["error"]
    assert "previous plan had problems" in llm.inputs[-2][1]
    assert "command not found" in llm.inputs[-1][1]  # the fix sees the real output

    tut = client.get(f"/api/tutorials/{job['result']['tutorial_id']}", headers=_auth(client)).json()
    spec = tut["spec"]
    assert spec["rehearsal"]["repairs"] == 1
    run = next(s for s in spec["steps"] if s["id"] == "run")
    assert run["action"]["command"] == "cowsay hi"
    assert spec["steps"][0]["action"]["command"].startswith("DEBIAN_FRONTEND=noninteractive")
    ids = [s["id"] for s in tut["graph"]["states"]]
    assert ids == ["title", "install", "run", "open", "reload", "outro"]
    boxes = _sandboxes(client)
    assert len(boxes) == 2 and all(b["status"] == "terminated" for b in boxes)
    msgs = client.get(f"/api/conversations/{cid}", headers=_auth(client)).json()["messages"]
    assert any("here's the plan" in m["content"] for m in msgs)


def test_unfixable_rehearsal_records_nothing(client, monkeypatch):
    fix = StepFix(before=[], fixable=False, reason="needs a GPU", step=_step("run", command="x"))
    llm = StubLLM([_plan(GOOD), _plan(GOOD)], [fix, fix])
    _setup(client, monkeypatch, llm)
    _, jid = _send(client)
    _answer(client, jid, pipeline.SKIP)
    job = _wait(client, jid, {"succeeded", "failed"})
    assert job["status"] == "failed" and "failed twice" in job["error"]
    assert "needs a GPU" in job["error"]
    assert "failed when run for real" in llm.inputs[3][1]  # the re-plan saw the failure
    boxes = _sandboxes(client)
    assert len(boxes) == 2 and all(b["status"] == "terminated" for b in boxes)  # no recording
    assert client.get("/api/tutorials", headers=_auth(client)).json() == []


def test_infeasible_task_is_refused_without_sandbox(client, monkeypatch):
    _setup(client, monkeypatch, StubLLM([_plan([], feasible=False, reason="It needs macOS.")]))
    cid, jid = _send(client, "Make a tutorial for Final Cut Pro")
    job = _wait(client, jid, {"succeeded", "failed"})
    assert job["status"] == "succeeded" and job["result"]["planned"] is False
    msgs = client.get(f"/api/conversations/{cid}", headers=_auth(client)).json()["messages"]
    assert "It needs macOS." in msgs[-1]["content"]
    assert _sandboxes(client) == []


def test_plan_without_installs_still_asks_before_running(client, monkeypatch):
    _setup(client, monkeypatch, StubLLM([_plan(GOOD[2:])]))
    _, jid = _send(client)
    job = _answer(client, jid, pipeline.CANCEL)
    assert job["pending_input"]["options"] == [pipeline.RUN, pipeline.CANCEL]
    job = _wait(client, jid, {"succeeded", "failed"})
    assert job["result"]["cancelled"] is True
    assert _sandboxes(client) == []


def test_problems_rejects_unsafe_and_malformed_steps():
    assert planner.problems(_plan(GOOD)) == []
    assert "at least 120" in planner.problems(_plan(GOOD, intro="Hi."))[0]
    bad = [
        _step("a", command="reboot"),
        _step("b", command="rm -rf / --no-preserve-root"),
        _step("c", command="nano /etc/hosts"),
        _step("d", command="echo 1\necho 2"),
        _step("Bad Id", command="ls"),
        _step("e", "open_url"),
        _step("outro", "key"),
    ]
    out = "; ".join(planner.problems(_plan(bad)))
    for frag in ("a: command", "b: command", "c: command", "d: terminal", "'Bad Id'",
                 "e: open_url", "outro"):  # fmt: skip
        assert frag in out, frag
    assert planner.problems(_plan([_step("vimrc", command="cat ~/.vimrc"),
                                   _step("ls", command="ls -la")])) == []  # fmt: skip


def test_to_spec_matches_recipe_format():
    spec = planner.to_spec(_plan(GOOD), "goal")
    install = spec["steps"][0]
    assert install["action"]["ask"] is True and install["narration"] == ["install", "install_done"]
    assert install["zoom_out_after"] is True
    assert spec["steps"][1]["action"]["ask"] is False
    assert spec["steps"][2]["action"] == {"kind": "open_url", "url": "localhost", "app": "browser"}
    assert set(spec["lines"]) == {"intro", "outro", "install", "install_done", "run", "open",
                                  "reload"}  # fmt: skip
    assert spec["say"] == {"engine-x": "nginx"}


def test_output_lines_strips_command_and_prompt():
    tail = "\x1b[1muser@cueframe\x1b[0m:/work$ cowsay hi\r\n ____\n< hi >\n ----\n"
    tail += "user@cueframe:/work$ "
    assert planner.output_lines({"output_tail": tail}, "cowsay hi") == [" ____", "< hi >", " ----"]


def test_caption_words_use_plan_spellings():
    words = [Word(text="Install", t=0, end=0.2), Word(text="gee-imp.", t=0.2, end=0.5)]
    assert [w.text for w in caption_words(words, {"gee-imp": "GIMP"})] == ["Install", "GIMP."]


def test_multi_word_respellings_are_undone_on_screen():
    text = ["install", "J", "Q,", "then", "two", "hundred"]
    words = [Word(text=t, t=i, end=i + 0.5) for i, t in enumerate(text)]
    assert [w.text for w in caption_words(words, {"j q": "jq"})] == [
        "install",
        "jq,",
        "then",
        "200",
    ]
    plan = _plan([_step("install", command="apt-get install -y jq", label="Install J Q",
                        badge="J Q ready")])  # fmt: skip
    plan.say = [planner.SayAs(written="jq", spoken="J Q")]
    spec = planner.to_spec(plan, "g")
    assert spec["steps"][0]["label"] == "Install jq" and spec["steps"][0]["badge"] == "jq ready"


def test_zoom_keeps_long_commands_in_frame():
    assert planner.zoom_for("ls") == 1.8
    assert (
        1.0 < planner.zoom_for("curl -s 'https://api.github.com/repos/jqlang/jq' -o c.json") < 1.8
    )
    assert planner.zoom_for("x" * 200) == 1.0


def test_title_and_outro_copy_come_from_the_plan():
    spec = planner.to_spec(
        _plan(
            [
                _step(
                    "install",
                    command="DEBIAN_FRONTEND=noninteractive apt-get install -y -q cowsay | head",
                    installs=True,
                ),
                *GOOD[1:],
            ]
        ),
        "goal",
    )
    assert build.teaser(spec["steps"]) == "$ apt-get install -y -q cowsay"
    assert spec["done"] == "Cowsay says hi"
