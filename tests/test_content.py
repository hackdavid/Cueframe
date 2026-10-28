from app.handlers.tutorial import Route, _format, _seconds
from app.tutorial import build, formats, pipeline, planner
from app.tutorial.formats import Beat, Storyboard
from tests.test_planner import GOOD, StubLLM, _answer, _plan, _send, _setup, _wait, plans

BOARD = Storyboard(
    angle="A tiny tool that 4,000 developers starred in a day",
    beats=[
        Beat(purpose="hook", seconds=4, on_screen="repo page header", line="the star count",
             fact="4,000 stars today (GitHub Trending)", pace="real"),
        Beat(purpose="step", seconds=8, on_screen="install output", line="install it",
             fact="", pace="fast"),
    ],
    caption="4,000 stars in a day.",
    hashtags=["#opensource", "dev tools", "github"],
)  # fmt: skip


class AskingLLM(StubLLM):
    async def parse(self, schema, **kw):
        if schema is Route:
            self.inputs.append(("Route", kw.get("input", "")))
            return Route(intent="make_tutorial"), None
        if schema is Storyboard:
            self.inputs.append(("Storyboard", kw.get("instructions", "")))
            return BOARD.model_copy(deep=True), None
        return await super().parse(schema, **kw)


def test_chat_asks_format_and_length_then_storyboards_the_plan(client, monkeypatch):
    llm = AskingLLM([_plan(GOOD[2:])])
    _setup(client, monkeypatch, llm)
    _, jid = _send(client, "Give me a trending topic to make content about")
    job = _answer(client, jid, "Tutorial: a step-by-step guide")
    assert job["pending_input"]["options"] == list(formats.CHOICES)
    job = _answer(client, jid, "90 seconds")
    assert job["pending_input"]["options"] == ["60 seconds", "90 seconds", "3 minutes"]
    job = _answer(client, jid, pipeline.CANCEL)
    assert job["pending_input"]["options"] == [pipeline.RUN, pipeline.CANCEL]
    job = _wait(client, jid, {"succeeded", "failed"})
    assert job["status"] == "succeeded", job["error"]
    skill = next(i for n, i in llm.inputs if n == "Storyboard")
    assert "step-by-step guide" in skill and "about 90 seconds" in skill
    planned = plans(llm)[0]
    assert "STORYBOARD:" in planned and "fact: 4,000 stars today" in planned


def test_format_named_in_the_message_is_not_asked_again():
    assert _seconds("make a 2 min reel") == 120 and _seconds("a 45s short") == 45
    assert _seconds("make a reel about it") is None


def test_format_words_back_up_the_router():
    assert _format("Make an explainer video about today's top AI paper") == "explainer"
    assert _format("a 30s short about it") == "reel"
    assert _format("step-by-step guide to nginx") == "tutorial"
    assert _format("give me a trending topic to make content about") is None


def test_word_budget_follows_the_chosen_length():
    assert planner.words("influencer", 30) == (40, 68)
    assert planner.words("influencer") == planner.REEL_WORDS
    steps = GOOD[2:]
    long = _plan(steps, intro=" ".join(["word"] * 100))
    assert any("explainer videos need 40-68" in p for p in planner.problems(long, "explainer", 30))
    assert not [p for p in planner.problems(long, "explainer", 60) if "words" in p]
    sys = planner.instructions("explainer", 90, formats.skill("explainer"))
    assert "about 90 seconds" in sys and "STYLE OVERRIDE" in sys and "Problem:" in sys


def test_fast_and_cut_steps_play_the_real_recording_sped_up():
    assert build.pace_rate("real", 30) == 1 and build.pace_rate("fast", 2.5) == 1
    assert build.pace_rate("fast", 10) == 5 and build.pace_rate("fast", 60) == 8
    assert build.pace_rate("cut", 60) == 16
    step = {
        "id": "install",
        "label": "Install",
        "badge": "Installed",
        "highlight": {},
        "action": {"kind": "terminal", "command": "pip install -q scikit-learn"},
    }
    rec = {"ok": True, "started": 100.0, "ended": 110.0, "t_in": 5.0, "t_out": 15.5,
           "returncode": 0}  # fmt: skip
    real = build.plan_step(step, rec, [], None)
    fast = build.plan_step({**step, "pace": "fast"}, rec, [], None)
    assert real.source.rate == 1 and fast.source.rate == 5
    assert (fast.source.t_in, fast.source.t_out) == (real.source.t_in, real.source.t_out)
    assert fast.duration < real.duration
    failed = build.plan_step({**step, "pace": "fast"}, {**rec, "ok": False}, [], None)
    assert failed.source.rate == 1  # a failure is never hidden by speeding it up


def test_storyboard_shows_in_chat_and_brings_caption_and_hashtags():
    sb = BOARD.model_copy(deep=True)
    text = formats.describe(sb, "influencer", 60)
    assert "Storyboard: reel, about 60 seconds" in text and "| 2 | step |" in text
    assert "fast-forward" in text
    assert "fact: 4,000 stars today" in formats.board_text(sb)
    social = formats.social({"caption": sb.caption, "hashtags": ["opensource", "github"]})
    assert "4,000 stars in a day." in social and "#opensource #github" in social


def test_hashtags_are_spelled_from_known_words():
    from app.tutorial.formats import hashtags

    text = "Who won the world series 2026? Google Trends shows 1M+ searches"
    tags = ["trendingnow", "googlettrends", "#ContentIdeas", "worldseries", "WorldSeries",
            "creatortips", "zxqvbn", "series2026"]  # fmt: skip
    assert hashtags(tags, text) == [
        "trendingnow",
        "googletrends",
        "ContentIdeas",
        "worldseries",
        "creatortips",
        "series2026",
    ]


def test_confirmation_names_the_chosen_format():
    from app.tutorial.pipeline import kind

    assert kind({"mode": "influencer"}) == "reel"
    assert kind({"mode": "explainer"}) == "explainer"
    assert kind({}) == "tutorial"


def test_operator_voice_is_a_problem_and_has_a_last_resort():
    from app.tutorial import planner

    assert planner.FIRST_PERSON.search("I will open the export menu.")
    assert planner.FIRST_PERSON.search("Let me scroll down")
    assert not planner.FIRST_PERSON.search("You open the export menu.")
    assert planner.viewer_voice("I will open the export menu.") == "Open the export menu."
    assert planner.SPOKEN_ISSUE.match(f"step a: {planner.FIRST_PERSON_ISSUE} ('I will')")
