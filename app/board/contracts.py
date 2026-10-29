"""Board plan: the model's lesson as a list of board operations in math coordinates.
The renderer maps them to pixels; the model never writes drawing code."""

import re
from typing import Literal

from pydantic import BaseModel, Field

Color = Literal["ink", "blue", "red", "green", "orange", "purple"]
Level = Literal["child", "school", "undergrad"]
ROW = ("write", "text", "section")  # ops that take the next row of the board
MARKS = ("box", "underline", "cross", "arrow")  # ops that annotate an earlier row
FIGURE = ("plot", "area", "riemann", "tangent", "point", "segment", "polygon", "circle",
          "angle", "label")  # fmt: skip
OpKind = Literal[
    "write", "text", "section", "box", "underline", "cross", "arrow", "figure", "plot",
    "area", "riemann", "tangent", "point", "segment", "polygon", "circle", "angle", "label",
]  # fmt: skip


class Op(BaseModel):
    kind: OpKind = Field(
        description="write: a LaTeX line on the next row; text: a short note in words; "
        "section: a heading that starts a new part of the board; box/underline/cross: mark "
        "an earlier row; arrow: curved arrow from one row to another; figure: start a "
        "diagram (axes for graphs, a blank plane for geometry) beside the next rows; "
        "plot/area/riemann/tangent: on the current figure's axes; point/segment/polygon/"
        "circle/angle/label: geometry on the current figure"
    )
    id: str = Field("", description="Handle later ops can refer to, e.g. 'l3'; '' if none")
    cue: str = Field(
        "",
        description="One word copied from this step's `say`: the op starts as it is "
        "spoken. '' to follow the previous op",
    )
    latex: str = Field(
        "",
        description="write: one line of LaTeX math, no $ (e.g. '2x + 3 = 7'); "
        "section: the heading in plain words; label/segment/point/angle/plot: an optional "
        "LaTeX label, e.g. '5', '90^\\\\circ', 'y=x^2'",
    )
    text: str = Field("", description="text: the note in plain words; arrow: a tiny label "
                      "like '÷2'; else ''")  # fmt: skip
    target: str = Field("", description="box/underline/cross/arrow: id of an earlier write or "
                        "text op; write: id of the line this one is rewritten from (it morphs "
                        "out of it), else ''")  # fmt: skip
    to: str = Field("", description="arrow: id of the row it points to")
    expr: str = Field("", description="plot/area/riemann/tangent: f(x) in SymPy syntax with "
                      "explicit *, e.g. 'x**2', 'x*exp(x)', 'sin(x)'")  # fmt: skip
    a: float = Field(0, description="area/riemann: from x = a")
    b: float = Field(0, description="area/riemann: to x = b")
    n: int = Field(0, description="riemann: number of rectangles (2-40)")
    at: float = Field(0, description="tangent: the x where the tangent touches")
    x_range: list[float] = Field([], description="figure: [xmin, xmax]")
    y_range: list[float] = Field([], description="figure: [ymin, ymax]")
    axes: bool = Field(False, description="figure: true draws numbered axes (graphs, "
                       "calculus), false a blank plane with equal scale (geometry)")  # fmt: skip
    points: list[str] = Field(
        [],
        description="segment [A,B]; polygon [A,B,C,...]; angle [A,B,C] marks the angle "
        "at B; circle [O] (with radius) or [O,P] (through P); label [A]",
    )
    x: float = Field(0, description="point: x coordinate in math units; label: x if no point")
    y: float = Field(0, description="point: y coordinate; label: y if no point")
    radius: float = Field(0, description="circle: radius in math units when points is [O]")
    color: Color = "ink"


class Check(BaseModel):
    """One claim on the board, verified with SymPy before anything is drawn."""

    kind: Literal[
        "equal", "equivalent", "solves", "derivative", "antiderivative", "definite",
        "limit", "value", "geometry",
    ] = Field(
        description="equal: a and b are the same expression; equivalent: equations a and b "
        "have the same solutions; solves: values are exactly the solutions of equation a; "
        "derivative: d/dvar a = b; antiderivative: b is an antiderivative of a; definite: "
        "integral of a from lower to upper = b; limit: limit of a as var -> lower = b; "
        "value: number a = number b; geometry: a = b where both use point names and "
        "dist(A,B), angle(A,B,C) in degrees, area(A,B,C,...), slope(A,B)"
    )  # fmt: skip
    a: str = Field(description="SymPy syntax with explicit *, e.g. '(x+1)**2' or '2*x+3=7'")
    b: str = Field("", description="SymPy syntax")
    var: str = "x"
    lower: str = ""
    upper: str = ""
    values: list[str] = []
    why: str = Field("", description="What this checks, e.g. 'expand the square'")


class Step(BaseModel):
    say: str = Field(description="What the teacher says while writing this step: 1-3 short "
                     "spoken sentences, no LaTeX (say 'x squared', not x^2)")  # fmt: skip
    ops: list[Op]
    checks: list[Check] = []


class BoardPlan(BaseModel):
    """LLM contract: a complete board lesson."""

    feasible: bool = Field(description="False if this isn't a math question the board can teach")
    reason: str = Field("", description="Why not, when feasible is false")
    title: str = Field(description="Video title, max 60 characters")
    level: Level
    problem: str = Field(description="The problem in plain words, max 160 characters")
    answer: str = Field(description="The final answer as LaTeX, e.g. 'x = 2'")
    steps: list[Step]
    recap: list[str] = Field([], description="2-4 short takeaways")


def _balanced(s: str) -> bool:
    depth = 0
    for ch in re.sub(r"\\[{}]", "", s):
        depth += {"{": 1, "}": -1}.get(ch, 0)
        if depth < 0:
            return False
    return depth == 0


def normalize(plan: BoardPlan) -> BoardPlan:
    """Fix what has only one sensible reading: a figure that gets a plot needs axes."""
    fig: Op | None = None
    for step in plan.steps:
        for op in step.ops:
            if op.kind == "figure":
                fig = op
            elif fig is not None and op.kind in ("plot", "area", "riemann", "tangent"):
                fig.axes = True
    return plan


def problems(plan: BoardPlan) -> list[str]:
    """Structural problems that would make the board wrong or unrenderable."""
    out: list[str] = []
    if not plan.steps:
        return ["the plan has no steps"]
    if len(plan.steps) > 40:
        out.append(f"{len(plan.steps)} steps: keep it to 40 or fewer")
    ids: dict[str, str] = {}
    points: set[str] = set()
    figure: Op | None = None
    for si, step in enumerate(plan.steps, 1):
        if not step.say.strip():
            out.append(f"step {si}: nothing is said")
        if re.search(r"\\[a-zA-Z]+|\^|_\{", step.say):
            out.append(f"step {si}: `say` contains LaTeX; spell it out in words")
        for oi, op in enumerate(step.ops, 1):
            at = f"step {si} op {oi} ({op.kind})"
            if op.id and op.id in ids:
                out.append(f"{at}: id {op.id!r} is used twice")
            for ref, field in ((op.target, "target"), (op.to, "to")):
                if ref and ids.get(ref) not in ("write", "text"):
                    out.append(f"{at}: {field} {ref!r} is not an earlier write/text op")
            if op.kind == "write" and not op.latex.strip():
                out.append(f"{at}: no latex")
            if op.kind == "text" and not op.text.strip():
                out.append(f"{at}: no text")
            if op.latex and not _balanced(op.latex):
                out.append(f"{at}: unbalanced braces in {op.latex!r}")
            if op.kind in MARKS and not op.target:
                out.append(f"{at}: needs a target")
            if op.kind == "arrow" and not op.to:
                out.append(f"{at}: needs `to`")
            if op.kind == "figure":
                for name, r in (("x_range", op.x_range), ("y_range", op.y_range)):
                    if len(r) != 2 or not r[0] < r[1]:
                        out.append(f"{at}: {name} must be [min, max] with min < max")
                figure, points = op, set()
            elif op.kind in FIGURE and figure is None:
                out.append(f"{at}: draw a figure first")
            if op.kind in ("plot", "area", "riemann", "tangent"):
                if figure is not None and not figure.axes:
                    out.append(f"{at}: needs a figure with axes=true")
                if not op.expr:
                    out.append(f"{at}: needs expr")
            if op.kind in ("area", "riemann") and not op.a < op.b:
                out.append(f"{at}: needs a < b")
            if op.kind == "riemann" and not 2 <= op.n <= 40:
                out.append(f"{at}: n must be 2-40")
            if op.kind == "point":
                if not op.latex and not op.id:
                    out.append(f"{at}: name the point in latex (e.g. 'A')")
                points.add(op.latex or op.id)
            need = {"segment": 2, "polygon": 3, "angle": 3, "circle": 1}.get(op.kind)
            if need and len(op.points) < need:
                out.append(f"{at}: needs {need} points")
            for p in op.points:
                if p not in points:
                    out.append(f"{at}: point {p!r} isn't drawn on this figure yet")
            if op.kind == "circle" and len(op.points) == 1 and op.radius <= 0:
                out.append(f"{at}: needs a radius or a second point")
            if op.id:
                ids[op.id] = op.kind
    return out
