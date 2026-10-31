"""Narration dedup: the same sentence must never be spoken twice in one video."""

from app.tutorial import dedup
from app.tutorial.planner import PlannedStep, TutorialPlan, problems


def test_verbatim_repeat_is_flagged():
    lines = {"a": "The repo has over twelve thousand stars.",
             "b": "The repo has over twelve thousand stars."}  # fmt: skip
    assert dedup.duplicates(lines)


def test_near_repeat_is_flagged():
    lines = {"a": "This project has over twelve thousand stars on GitHub.",
             "b": "This project has over twelve thousand stars on GitHub today."}  # fmt: skip
    assert dedup.duplicates(lines)


def test_jaccard_boundary():
    a = "one two three four five six seven eight nine ten"
    same7 = "one two three four five six seven alpha beta gamma"  # 7/13 = 0.54
    same8 = "one two three four five six seven eight alpha beta"  # 8/12 = 0.67
    same9 = "one two three four five six seven eight nine alpha"  # 9/11 = 0.82
    assert not dedup.pair_dup(a, same7)
    assert not dedup.pair_dup(a, same8)
    assert dedup.pair_dup(a, same9)


def test_steps_sharing_one_fact_phrase_are_not_repeats():
    # board steps often end on the same metric with a different point before it
    assert not dedup.pair_dup("Longer step 0, ours 48.7.", "Longer step 1, ours 48.7.")


def test_short_functional_lines_are_exempt():
    # near-identical filler below the token floor is fine...
    assert not dedup.pair_dup("Now run it.", "Now run it again.")
    # ...but a truly identical line is a repeat however short it is
    assert dedup.pair_dup("The repo has 12,345 stars.", "The repo has 12,345 stars.")


def test_punctuation_and_case_dont_hide_a_repeat():
    assert dedup.pair_dup("It has 12,345 stars!", "it has 12 345 stars")


def test_distinct_lines_pass():
    lines = {"a": "Install it with one pip command.",
             "b": "The README walks through the configuration options."}  # fmt: skip
    assert dedup.duplicates(lines) == []


def test_repeats_returns_the_key():
    lines = {"step1": "The graph climbs every single week this year."}
    assert dedup.repeats("The graph climbs every single week this year.", lines) == "step1"
    assert dedup.repeats("A completely different sentence about code.", lines) is None


def _step(sid: str, narration: str, after: str = "") -> PlannedStep:
    base = dict(id=sid, label=sid.title(), kind="key", command="", url="", keys="Return",
                installs=False, expect="", narration=narration, after=after, badge="",
                highlight="", output_label="", success=False, app="", target="",
                text="", enter=False, at=[], to=[])  # fmt: skip
    return PlannedStep(**base)


def _plan(intro: str, outro: str, steps: list[tuple[str, str, str]]) -> TutorialPlan:
    return TutorialPlan(
        feasible=True, reason="", title="t", subtitle="", summary="", software="",
        tags=[], done="Done", intro=intro, outro=outro, say=[],
        steps=[_step(sid, n, a) for sid, n, a in steps],
    )  # fmt: skip


def test_problems_flags_a_repeated_line(monkeypatch):
    monkeypatch.setenv("CUEFRAME_DEDUP_ENFORCE", "1")
    p = _plan("Welcome to this video about the project.", "The repo has over twelve thousand stars.",
              [("open", "Let's open the project page in the browser now.", ""),
               ("stars", "The repo has over twelve thousand stars.", "")])  # fmt: skip
    assert any("say the same thing" in x for x in problems(p, "tutorial"))


def test_problems_passes_without_enforce(monkeypatch):
    monkeypatch.delenv("CUEFRAME_DEDUP_ENFORCE", raising=False)
    p = _plan("Welcome.", "Same line spoken twice here today for sure.",
              [("a", "Same line spoken twice here today for sure.", ""),
               ("b", "Something entirely different about the code.", "")])  # fmt: skip
    assert not any("say the same thing" in x for x in problems(p, "tutorial"))


def test_reworded_outro_recap_passes(monkeypatch):
    monkeypatch.setenv("CUEFRAME_DEDUP_ENFORCE", "1")
    p = _plan("Welcome.", "And that's the whole setup, ready to use.",
              [("a", "The install finishes with the version printed out.", ""),
               ("b", "Configuration lives in one small file you edit.", "")])  # fmt: skip
    assert not any("say the same thing" in x for x in problems(p, "tutorial"))
