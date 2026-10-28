import asyncio

from app.tutorial import planner
from app.tutorial.planner import Retold, SpokenLine, TutorialPlan
from tests.test_planner import GOOD, LONG, _plan


class _LLM:
    configured = True

    def __init__(self, plans, retold):
        self.plans, self.retold, self.calls = list(plans), list(retold), []

    async def parse(self, schema, **kw):
        self.calls.append((schema.__name__, kw.get("reasoning_effort")))
        if schema is TutorialPlan:
            return self.plans.pop(0), None
        if schema is Retold:
            return self.retold.pop(0), None
        raise AssertionError(schema)


class _Ctx:
    def __init__(self, llm):
        self.llm, self.notes = llm, []

        class S:
            research_model, plan_reasoning, fix_reasoning = "m", "medium", "low"

        self.settings = S()

    def phase(self, *a):
        pass

    def thinking(self, title, text):
        self.notes.append(text)


def _retold(steps, intro=LONG):
    return Retold(intro=intro, outro="Bye.",
                  steps=[SpokenLine(id=s.id, narration=f"Longer {s.id}.", after="")
                         for s in steps])  # fmt: skip


def test_short_narration_is_rewritten_without_a_new_plan(monkeypatch):
    async def research(ctx, goal):
        return ""

    monkeypatch.setattr(planner, "research", research)
    llm = _LLM([_plan(GOOD, intro="Too short.")], [_retold(GOOD)])
    spec = asyncio.run(planner.plan(_Ctx(llm), "say hi"))
    assert isinstance(spec, dict), spec
    assert [c[0] for c in llm.calls] == ["TutorialPlan", "Retold"]
    assert llm.calls[1][1] == "low"
    assert spec["lines"]["run"] == "Longer run." and spec["lines"]["intro"] == LONG
    assert spec["steps"][0]["action"]["command"].endswith("apt-get install -y cowsay")


def test_rewrite_that_drops_a_step_falls_back_to_a_new_plan(monkeypatch):
    async def research(ctx, goal):
        return ""

    monkeypatch.setattr(planner, "research", research)
    llm = _LLM([_plan(GOOD, intro="Too short."), _plan(GOOD)], [_retold(GOOD[:2])])
    spec = asyncio.run(planner.plan(_Ctx(llm), "say hi"))
    assert isinstance(spec, dict)
    assert [c[0] for c in llm.calls] == ["TutorialPlan", "Retold", "TutorialPlan"]
