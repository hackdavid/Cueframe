import pytest

from app.board import concept
from app.board.contracts import BoardPlan, problems


def _plan(**over):
    d = {
        "feasible": True, "title": "T", "level": "undergrad", "problem": "p", "steps": [
            {"say": "Before and after.", "ops": [
                {"kind": "figure", "x_range": [0, 10], "y_range": [0, 4]},
                {"kind": "node", "id": "a", "text": "Old way", "x": 2, "y": 2},
                {"kind": "node", "id": "b", "text": "New way", "x": 8, "y": 2},
                {"kind": "link", "points": ["a", "b"]}]},
            {"say": "Results.", "ops": [{"kind": "bars", "text": "Accuracy (%)",
                                         "labels": ["Base", "Ours"], "values": [41.2, 48.7]}]},
        ]}  # fmt: skip
    d.update(over)
    return BoardPlan.model_validate(d)


def test_concept_plan_is_valid_without_answer_or_checks():
    assert problems(_plan()) == []


def test_concept_contract_errors():
    p = _plan()
    p.steps[0].ops[3].points = ["a", "zz"]
    p.steps[1].ops[0].values = [1.0]
    out = " | ".join(problems(p))
    assert "node 'zz'" in out and "bars need 2-8 labels" in out


def test_numbers_must_come_from_source():
    p = _plan()
    assert concept.unsupported(p, "Baseline scores 41.2% and ours 48.7%.") == []
    bad = concept.unsupported(p, "Baseline scores 41.2%.")
    assert bad == ["step 2: the number 48.7 is not in the source facts"]


def test_paper_requests_route_to_concept_board():
    from app.tutorial.pipeline import _CONCEPT

    assert _CONCEPT.search("Make an explainer about this arXiv paper")
    assert not _CONCEPT.search("Make a tutorial on nginx")


def test_title_numbers_must_come_from_source():
    p = _plan(title="90% faster inference")
    assert concept.unsupported(p, "Baseline 41.2, ours 48.7.") == [
        "title/problem/recap: the number 90 is not in the source facts"
    ]


def test_trending_paper_and_model_requests_are_trending():
    from app.handlers.tutorial import _PAPER_OR_MODEL, _TRENDING

    for t in (
        "Make a 60 second explainer about today's #1 AI paper on Hugging Face",
        "Make a 45 second reel about today's top trending model on Hugging Face",
    ):
        assert _TRENDING.search(t) and _PAPER_OR_MODEL.search(t)
    t = "Make a tutorial on nginx"
    assert not (_TRENDING.search(t) and _PAPER_OR_MODEL.search(t))


def test_numbers_match_in_any_written_form():
    p = _plan(title="Accuracy 90.2 vs 1000")
    assert (
        concept.unsupported(p, "Base 41.2, ours 48.70; it reaches 90.20% on 1,000 prompts.") == []
    )


def test_close_but_different_numbers_are_rejected():
    for src, claim in (
        ("1000001 tokens", "1000002 tokens"),
        ("1,000,001 prompts", "1,000,002 prompts"),
    ):
        bad = concept.unsupported(_plan(title=claim), f"{src}; base 41.2, ours 48.7")
        assert bad == [
            f"title/problem/recap: the number {claim.split()[0].replace(',', '')} "
            "is not in the source facts"
        ]


def test_arrow_through_a_node_is_rejected():
    p = _plan()
    ops = p.steps[0].ops
    ops.insert(2, ops[1].model_copy(update={"id": "m", "text": "Middle", "x": 5}))
    assert "passes through node 'm'" in " | ".join(problems(p))


def test_bad_figure_range_is_reported_not_raised():
    p = _plan()
    p.steps[0].ops[0].y_range = [0]
    assert "y_range must be [min, max]" in " | ".join(problems(p))


def test_narration_must_fit_the_requested_length():
    p = _plan()
    assert concept.too_long(p, 60) == []
    p.steps[0].say = "word " * 200
    assert "cut it to at most 92 words" in concept.too_long(p, 60)[0]
    p.steps[0].say = "这是一个很长的中文句子" * 40
    assert concept.too_long(p, 30)


def test_nodes_without_ids_and_links_by_label_are_normalized():
    from app.board.contracts import normalize

    p = _plan()
    ops = p.steps[0].ops
    ops[1].id, ops[2].id = "", ""
    ops[3].points = ["Old way", "New way"]
    assert problems(normalize(p)) == []
    assert ops[3].points == ["old_way", "new_way"]


def test_missing_link_node_asks_for_a_node():
    p = _plan()
    p.steps[0].ops[3].points = ["a", "zz"]
    assert "add a `node` op with id 'zz'" in " | ".join(problems(p))


def test_link_by_id_is_kept_when_a_label_matches_another_id():
    from app.board.contracts import normalize

    p = _plan()
    ops = p.steps[0].ops
    ops[1].id, ops[1].text = "input", "Prompt"
    ops[2].id, ops[2].text = "output", "Input"
    ops[3].points = ["input", "output"]
    normalize(p)
    assert ops[3].points == ["input", "output"]


def test_nodes_that_almost_touch_are_rejected():
    p = _plan()
    ops = p.steps[0].ops
    ops[0].x_range, ops[0].y_range = [0, 10], [0, 4]
    ops[1].text, ops[1].x, ops[1].y = "Match means", 5, 2
    ops[2].text, ops[2].x, ops[2].y = "Steady image", 7.1, 2
    ops[3].points = [ops[1].id, ops[2].id]
    assert "almost touch" in " | ".join(problems(p))
    ops[2].x = 9
    assert "almost touch" not in " | ".join(problems(p))


def test_a_cramped_row_of_nodes_is_spread_out():
    from app.board.contracts import normalize

    p = _plan()
    ops = p.steps[0].ops
    ops[0].x_range, ops[0].y_range = [0, 10], [0, 4]
    ops[1].text, ops[1].x, ops[1].y = "Match means", 5, 2
    ops[2].text, ops[2].x, ops[2].y = "Steady image", 7.1, 2
    ops[3].points = [ops[1].id, ops[2].id]
    normalize(p)
    assert ops[2].x - ops[1].x > 3
    assert "almost touch" not in " | ".join(problems(p))


def test_a_row_too_wide_to_spread_keeps_its_error():
    from app.board.contracts import normalize

    p = _plan()
    ops = p.steps[0].ops
    ops[0].x_range, ops[0].y_range = [0, 10], [0, 4]
    extra = [
        ops[1].model_copy(update={"id": f"n{i}", "text": "Thirteen char", "x": 1 + 2 * i})
        for i in range(5)
    ]
    links = [ops[3].model_copy(update={"points": [f"n{i}", f"n{i + 1}"]}) for i in range(4)]
    p.steps[0].ops = [ops[0], *extra, *links]
    before = [n.x for n in extra]
    normalize(p)
    assert [n.x for n in extra] == before
    assert "fewer nodes in that row" in " | ".join(problems(p))


@pytest.mark.anyio
async def test_a_clean_plan_is_returned_on_the_first_try():
    from types import SimpleNamespace

    calls = []

    async def parse(*a, **k):
        calls.append(k["input"])
        return _plan(), None

    ctx = SimpleNamespace(
        llm=SimpleNamespace(parse=parse),
        settings=SimpleNamespace(research_model="m"),
        thinking=lambda *a: None,
        reply=lambda *a: None,
    )
    p = await concept.plan(ctx, "Baseline 41.2, ours 48.7.", "explainer", 60, None)
    assert p.title == "T" and len(calls) == 1
    assert f"at most {concept.word_limit(60, 8)} words" in calls[0]


def test_an_empty_figure_and_no_diagram_are_rejected():
    p = _plan()
    p.steps[0].ops = [p.steps[0].ops[0]]
    assert "nothing is drawn on this figure" in " | ".join(problems(p))
    assert concept.undrawn(p) and not concept.undrawn(_plan())
    p.steps[0].ops[0].axes = True
    assert "nothing is drawn" not in " | ".join(problems(p))


@pytest.mark.anyio
async def test_a_plan_without_a_diagram_is_planned_afresh_not_repaired():
    from types import SimpleNamespace

    calls = []
    bare = _plan()
    bare.steps[0].ops = [op for op in bare.steps[0].ops if op.kind not in ("node", "link")]

    async def parse(*a, **k):
        calls.append(k["input"])
        return (bare.model_copy(deep=True) if len(calls) == 1 else _plan()), None

    ctx = SimpleNamespace(
        llm=SimpleNamespace(parse=parse),
        settings=SimpleNamespace(research_model="m"),
        thinking=lambda *a: None,
        reply=lambda *a: None,
    )
    p = await concept.plan(ctx, "Baseline 41.2, ours 48.7.", "explainer", 60, None)
    assert len(calls) == 2 and "previous plan" not in calls[1]
    assert not concept.undrawn(p)
