import asyncio
import json

import httpx

from app.agents.trending import Tools, TrendBrief, TrendingAgent, brief_text, parse_github_trending
from app.tutorial import planner
from tests.test_planner import LONG, _plan, _step

ROW = """<article class="Box-row"><a href="/sponsors/x">Sponsor</a>
<h2 class="h3 lh-condensed"><a href="/acme/rocket" class="Link">acme / rocket</a></h2>
<p class="col-9 color-fg-muted my-1 pr-4">Fast &amp; tiny <b>rockets</b></p>
<span itemprop="programmingLanguage">Python</span>
<a href="/acme/rocket/stargazers" class="Link">12,345</a>
<span class="d-inline-block float-sm-right">1,075 stars today</span></article>"""


def test_github_trending_page_is_parsed():
    (r,) = parse_github_trending(f"<html>{ROW}</html>")
    assert r == {"repo": "acme/rocket", "url": "https://github.com/acme/rocket",
                 "description": "Fast & tiny rockets", "language": "Python", "stars": 12345,
                 "stars_gained": 1075, "period": "today"}  # fmt: skip


def _http():
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.host == "github.com" and req.url.path == "/trending":
            return httpx.Response(200, text=ROW)
        if req.url.host == "github.com":
            return httpx.Response(200, text='<meta name="description" content="Rockets.">')
        if req.url.host == "raw.githubusercontent.com" and req.url.path.endswith("/README.md"):
            return httpx.Response(200, text="# Rocket\n## Quick start\nIt flies.")
        if req.url.path == "/api/models/acme/flash":
            return httpx.Response(200, json={"likes": 3926, "downloads": 10})
        if req.url.path == "/acme/flash/raw/main/README.md":
            return httpx.Response(200, text="---\nlicense: mit\n---\n# Flash\n## Benchmarks\n42.3")
        return httpx.Response(404)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_model_tool_reads_the_hugging_face_card():
    asyncio.run(_model_tool())


async def _model_tool():
    async with _http() as http:
        r = await Tools(http).read_model("acme/flash")
        assert r["likes"] == 3926 and r["readme_headings"] == ["Flash", "Benchmarks"]
        assert "license" not in r["readme"]
        assert "not found" in await Tools(http).read_model("acme/missing")


def test_repo_tool_reads_readme_headings():
    asyncio.run(_repo_tool())


async def _repo_tool():
    async with _http() as http:
        r = await Tools(http).read_github_repo("acme/rocket")
        assert r["about"] == "Rockets." and r["readme_headings"] == ["Rocket", "Quick start"]
        assert await Tools(http).read_github_repo("../etc") == "repo must be owner/name"


BRIEF = TrendBrief(found=True, kind="repo", name="acme/rocket", source="GitHub Trending",
                   why_trending="1,075 stars today", url="https://github.com/acme/rocket",
                   extra_urls=[], one_liner="Tiny rockets.", facts=["It flies."],
                   show=["Quick start"], reason="top repo")  # fmt: skip


class Res:
    def __init__(self, output):
        self.output = output


class AgentLLM:
    configured = True

    def __init__(self):
        self.turns = [
            [{"type": "function_call", "name": "github_trending", "call_id": "a",
              "arguments": json.dumps({"since": "daily", "language": ""})}],
            [{"type": "function_call", "name": "read_github_repo", "call_id": "b",
              "arguments": json.dumps({"repo": "acme/rocket"})},
             {"type": "function_call", "name": "nope", "call_id": "c", "arguments": "{}"}],
            [{"type": "message", "content": [{"type": "output_text", "text": "acme/rocket"}]}],
        ]  # fmt: skip
        self.seen = []

    async def respond(self, **kw):
        self.seen.append(kw["input"])
        return Res(self.turns.pop(0))

    async def parse(self, schema, **kw):
        self.seen.append(kw["input"])
        return BRIEF, None


def test_agent_calls_tools_then_writes_brief():
    llm = AgentLLM()

    async def run():
        async with _http() as http:
            return await TrendingAgent(llm, "m", http).run("trending repo today")  # type: ignore[arg-type]

    brief, trace = asyncio.run(run())
    assert brief is BRIEF
    assert [t["tool"] for t in trace] == ["github_trending", "read_github_repo", "nope"]
    outputs = {
        o["call_id"]: o["output"] for o in llm.seen[-1] if o.get("type") == "function_call_output"
    }
    assert "acme/rocket" in outputs["a"] and "Quick start" in outputs["b"]
    assert outputs["c"].startswith("error:")  # an unknown tool is reported, not fatal
    assert "Facts from its own page (use only these):\n- It flies." in brief_text(BRIEF)


def test_influencer_plans_must_stay_under_a_minute():
    steps = [_step("open", "open_url", url="https://github.com/acme/rocket"),
             _step("scroll", "scroll", app="browser", target="Quick start")]  # fmt: skip
    long = planner.problems(_plan(steps, intro=LONG * 2), "influencer")
    assert any("influencer videos need" in p for p in long)
    short = _plan(steps, intro=" ".join(["word"] * 90))
    assert planner.problems(short, "influencer") == []
    assert any("at least" in p for p in planner.problems(short))  # tutorials stay longer
    assert planner.problems(_plan(steps, intro=LONG)) == []
    spec = planner.to_spec(short, "goal", "influencer")
    assert spec["mode"] == "influencer" and spec["kicker"].startswith("TRENDING")


class Ctx:
    class settings:  # noqa: N801
        research_model, plan_reasoning = "m", ""

    def __init__(self, llm):
        self.llm, self.notes = llm, []

    def phase(self, *_a):
        pass

    def thinking(self, title, text):
        self.notes.append(text)


class GroundLLM:
    configured = True

    def __init__(self, plans, claims):
        self.plans, self.claims, self.inputs = list(plans), list(claims), []

    async def parse(self, schema, **kw):
        self.inputs.append((schema.__name__, kw["input"]))
        if schema is planner.Claims:
            return planner.Claims(unsupported=self.claims.pop(0)), None
        return self.plans.pop(0), None


def test_trend_narration_is_fact_checked_against_the_brief():
    steps = [
        _step("open", "open_url", url="https://github.com/acme/rocket"),
        _step("start", "scroll", app="browser", target="Quick start"),
        _step("top", "scroll", app="browser", target="Rocket"),
    ]
    words = " ".join(["word"] * 90)
    llm = GroundLLM([_plan(steps, intro=words + " number one"), _plan(steps, intro=words)],
                    [["'number one': the brief gives no rank"], []])  # fmt: skip
    spec = asyncio.run(planner.plan(Ctx(llm), "g", mode="influencer", brief=brief_text(BRIEF)))
    assert isinstance(spec, dict) and spec["mode"] == "influencer"
    asked = [i for n, i in llm.inputs if n == "TutorialPlan"]
    assert "not backed by the source: 'number one'" in asked[1]
    assert "1,075 stars today" in asked[0]  # the brief replaces docs research
