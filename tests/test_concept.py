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
    assert "point 'zz'" in out and "bars need 2-8 labels" in out


def test_numbers_must_come_from_source():
    p = _plan()
    assert concept.unsupported(p, "Baseline scores 41.2% and ours 48.7%.") == []
    bad = concept.unsupported(p, "Baseline scores 41.2%.")
    assert bad == ["step 2: the number 48.7 is not in the source facts"]


def test_paper_requests_route_to_concept_board():
    from app.tutorial.pipeline import _CONCEPT

    assert _CONCEPT.search("Make an explainer about this arXiv paper")
    assert not _CONCEPT.search("Make a tutorial on nginx")
