"""Board plan: the model's lesson as a list of board operations in math coordinates.
The renderer maps them to pixels; the model never writes drawing code."""

import re
from typing import Literal

from pydantic import BaseModel, Field

Color = Literal["ink", "blue", "red", "green", "orange", "purple"]
Level = Literal["child", "school", "undergrad"]
ROW = ("write", "text", "section", "bars", "quote")  # ops that take the next row of the board
MARKS = ("box", "underline", "cross", "arrow")  # ops that annotate an earlier row
FIGURE = ("plot", "area", "riemann", "tangent", "point", "segment", "polygon", "circle",
          "angle", "label", "node", "link")  # fmt: skip
OpKind = Literal[
    "write", "text", "section", "formula", "box", "underline", "cross", "arrow", "figure",
    "plot", "area", "riemann", "tangent", "point", "segment", "polygon", "circle", "angle", "label",
    "node", "link", "bars", "quote",
]  # fmt: skip


class Op(BaseModel):
    kind: OpKind = Field(
        description="write: a LaTeX line on the next row; text: a short note in words; "
        "section: a heading that starts a new part of the board; formula: the formula or "
        "rule being used, in a card on the right side of the board (latex = the formula, "
        "text = its name); box/underline/cross: mark "
        "an earlier row; arrow: curved arrow from one row to another; figure: start a "
        "diagram (axes for graphs, a blank plane for geometry) beside the next rows; "
        "plot/area/riemann/tangent: on the current figure's axes; point/segment/polygon/"
        "circle/angle/label: geometry on the current figure; node: a labelled block of a "
        "diagram at (x, y) on the current figure (a blank plane, e.g. x_range [0,10], "
        "y_range [0,5]); link: an arrow from node points[0] to node points[1], text = a "
        "tiny label; bars: a bar chart row (labels + values, text = what is measured); "
        "quote: a short sentence quoted from the source, in a card"
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
    text: str = Field(
        "",
        description="text: the note in plain words; formula: its name, e.g. 'Quadratic "
        "formula'; arrow: a tiny label like '÷2'; else ''",
    )
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
    labels: list[str] = Field([], description="bars: one short label per bar")
    values: list[float] = Field([], description="bars: one number per bar, exactly as in "
                                "the source")  # fmt: skip


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
    answer: str = Field("", description="The final answer as LaTeX, e.g. 'x = 2'")
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
    labels: dict[str, str] = {}
    for step in plan.steps:
        for op in step.ops:
            if op.kind == "figure":
                fig, labels = op, {}
            elif fig is not None and op.kind in ("plot", "area", "riemann", "tangent"):
                fig.axes = True
            if fig is not None and op.kind == "node":
                op.id = op.id or _slug(op.text)
                labels[op.text.strip().lower()] = op.id
            if fig is not None and op.kind == "link":  # nodes named by their label
                op.points = [labels.get(q.strip().lower(), q) for q in op.points]
    return plan


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")[:24] or "node"


def _crossed(fig: Op, nodes: dict[str, tuple[float, float]], ends: list[str]) -> list[str]:
    """Other nodes lying on the straight arrow between two nodes of a figure."""
    if len(ends) != 2 or not all(e in nodes for e in ends):
        return []
    if len(fig.x_range) != 2 or len(fig.y_range) != 2:
        return []  # the range itself is reported as a problem
    sx = (fig.x_range[1] - fig.x_range[0]) or 1
    sy = (fig.y_range[1] - fig.y_range[0]) or 1
    (ax, ay), (bx, by) = ((nodes[e][0] / sx, nodes[e][1] / sy) for e in ends)
    dx, dy = bx - ax, by - ay
    length2 = dx * dx + dy * dy or 1e-9
    out = []
    for name, (x, y) in nodes.items():
        if name in ends:
            continue
        px, py = x / sx - ax, y / sy - ay
        t = (px * dx + py * dy) / length2
        if 0.05 < t < 0.95 and abs(px * dy - py * dx) / length2**0.5 < 0.08:
            out.append(name)
    return out


def problems(plan: BoardPlan) -> list[str]:
    """Structural problems that would make the board wrong or unrenderable."""
    out: list[str] = []
    if not plan.steps:
        return ["the plan has no steps"]
    if len(plan.steps) > 40:
        out.append(f"{len(plan.steps)} steps: keep it to 40 or fewer")
    ids: dict[str, str] = {}
    order: dict[str, int] = {}  # row number of each write/text/section op
    points: set[str] = set()
    figure: Op | None = None
    nodes: dict[str, tuple[float, float]] = {}
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
                if ref and ids.get(ref) not in ("write", "text", "formula"):
                    out.append(f"{at}: {field} {ref!r} is not an earlier write/text/formula op")
            if op.kind in ("write", "formula") and not op.latex.strip():
                out.append(f"{at}: no latex")
            if op.kind in ("node", "quote", "bars") and not op.text.strip():
                out.append(f"{at}: needs text")
            if op.kind == "node" and (not op.id or len(op.text) > 28):
                out.append(f"{at}: a node needs an id and a label of at most 28 characters")
            if op.kind == "link" and len(op.points) != 2:
                out.append(f"{at}: a link needs points [from_node, to_node]")
            elif op.kind == "link" and figure is not None:
                for name in _crossed(figure, nodes, op.points):
                    out.append(f"{at}: the arrow passes through node {name!r}; link neighbours "
                               "only, or move that node off the line")  # fmt: skip
            if op.kind == "bars" and not (2 <= len(op.values) == len(op.labels) <= 8):
                out.append(f"{at}: bars need 2-8 labels and the same number of values")
            if op.kind == "text" and not op.text.strip():
                out.append(f"{at}: no text")
            if op.latex and not _balanced(op.latex):
                out.append(f"{at}: unbalanced braces in {op.latex!r}")
            if op.kind in MARKS and not op.target:
                out.append(f"{at}: needs a target")
            if op.kind == "arrow" and not op.to:
                out.append(f"{at}: needs `to`")
            far = op.target in order and op.to in order and abs(order[op.to] - order[op.target]) > 2
            if op.kind == "arrow" and far:
                out.append(f"{at}: an arrow only joins neighbouring lines; {op.target!r} "
                               f"and {op.to!r} are too far apart")  # fmt: skip
            if op.kind == "node" and op.id:
                nodes[op.id] = (op.x, op.y)
            if op.kind == "figure":
                for name, r in (("x_range", op.x_range), ("y_range", op.y_range)):
                    if len(r) != 2 or not r[0] < r[1]:
                        out.append(f"{at}: {name} must be [min, max] with min < max")
                figure, points, nodes = op, set(), {}
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
            if op.kind == "node" and op.id:
                points.add(op.id)
            if op.kind == "point":
                if not op.latex and not op.id:
                    out.append(f"{at}: name the point in latex (e.g. 'A')")
                points.update(n for n in (op.id, op.latex) if n)
            need = {"segment": 2, "polygon": 3, "angle": 3, "circle": 1}.get(op.kind)
            if need and len(op.points) < need:
                out.append(f"{at}: needs {need} points")
            for p in op.points:
                if p not in points and op.kind == "link":
                    out.append(f"{at}: node {p!r} isn't on this figure yet: add a `node` "
                               f"op with id {p!r} and a short label before this link")  # fmt: skip
                elif p not in points:
                    out.append(
                        f"{at}: point {p!r} isn't drawn on this figure yet: add a "
                        f"`point` op with id {p!r} after this figure's `figure` op"
                    )
            if op.kind == "circle" and len(op.points) == 1 and op.radius <= 0:
                out.append(f"{at}: needs a radius or a second point")
            if op.id:
                ids[op.id] = op.kind
            if op.kind in ROW:
                order[op.id or f"_{si}.{oi}"] = len(order)
    return out
