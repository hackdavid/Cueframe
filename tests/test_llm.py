from app.board.contracts import BoardPlan
from app.llm import repair_keys


def test_garbled_keys_are_restored_at_every_level():
    data = {
        "feasible": True,
        "level": "undergrad",
        "problem": "p",
        "title": "IDM",
        "answer前方": "",
        "steps": [
            {
                "say": "Look.",
                "ops": [
                    {
                        "kind": "figure",
                        "x_range phenomena": [0, 10],
                        "y_range venue": [0, 4],
                        "axesNormals": False,
                    },
                    {
                        "kind": "bars",
                        "labels once": ["A", "B"],
                        "values orbit": [1, 2],
                        "text telescope": "score",
                    },
                ],
            }
        ],
    }
    assert repair_keys(BoardPlan, data) == 7
    ops = BoardPlan.model_validate(data).steps[0].ops
    assert (ops[0].x_range, ops[0].y_range) == ([0, 10], [0, 4])
    assert (ops[1].labels, ops[1].values, ops[1].text) == (["A", "B"], [1, 2], "score")


def test_a_real_key_is_never_overwritten_or_misread():
    data = {"kind": "point", "x": 1, "x junk": 5, "attempt": 3}
    repair_keys(BoardPlan.model_fields["steps"].annotation.__args__[0], {"ops": [data]})
    assert data["x"] == 1 and "x junk" in data and "attempt" in data
