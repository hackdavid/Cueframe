"""Verify every claim of a board plan with SymPy before anything is drawn."""

import json
import random
import re
import signal
import sys

import sympy as sp

from app.board.contracts import BoardPlan, Check
from app.board.mathexpr import FUNCS, MathError, expr, relation

TOL = 1e-7


def _num(e: sp.Expr, subs: dict[sp.Symbol, float]) -> complex | None:
    try:
        v = complex(sp.N(e.subs(subs), 30))
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    return v if v == v and abs(v) != float("inf") else None


def is_zero(e: sp.Expr) -> bool:
    """True if `e` is identically zero: numeric samples first (fast, never hangs on
    huge expressions), then SymPy's simplify when the samples can't be evaluated."""
    syms = sorted(e.free_symbols, key=str)
    if not syms:
        v = _num(e, {})
        if v is not None:
            return abs(v) < TOL
    rnd = random.Random(7)
    vals = []
    for _ in range(12):
        v = _num(e, {s: rnd.uniform(0.3, 2.7) for s in syms})
        if v is not None:
            vals.append(v)
        if len(vals) >= 6:
            break
    if len(vals) >= 3:
        return all(abs(v) < TOL * 10 for v in vals)
    try:
        return sp.simplify(e) == 0
    except Exception:  # noqa: BLE001
        return False


def _points(plan: BoardPlan) -> dict[str, sp.Point2D]:
    pts = {}
    for step in plan.steps:
        for op in step.ops:
            if op.kind == "point":
                p = sp.Point2D(sp.nsimplify(op.x), sp.nsimplify(op.y))
                pts.update({n: p for n in (op.id, op.latex) if n.isidentifier()})
    return pts


def _geometry(pts: dict[str, sp.Point2D]) -> dict[str, object]:
    def angle(a: sp.Point2D, b: sp.Point2D, c: sp.Point2D) -> sp.Expr:
        u, v = a - b, c - b
        o = sp.Point2D(0, 0)
        return sp.acos((u.x * v.x + u.y * v.y) / (u.distance(o) * v.distance(o))) * 180 / sp.pi

    def area(*p: sp.Point2D) -> sp.Expr:
        return sp.Abs(sp.Polygon(*p).area)

    return {
        **pts,
        "dist": lambda a, b: a.distance(b),
        "angle": angle,
        "area": area,
        "slope": lambda a, b: (b.y - a.y) / (b.x - a.x),
    }


def _solutions(e: sp.Expr, x: sp.Symbol) -> set[sp.Expr] | None:
    try:
        s = sp.solveset(e, x, domain=sp.S.Reals)
    except Exception:  # noqa: BLE001
        return None
    return set(s) if isinstance(s, sp.FiniteSet) else None


def check(c: Check, plan: BoardPlan) -> str | None:
    """None if the claim holds, else what is wrong (with the expected value)."""
    x = sp.Symbol(c.var or "x")
    names = {c.var or "x": x}
    try:
        if c.kind == "geometry":
            names = _geometry(_points(plan))
            used = set(re.findall(r"[A-Za-z_]\w*", f"{c.a} {c.b}"))
            if missing := sorted(used - set(names) - set(FUNCS)):
                return f"no drawn point named {', '.join(missing)}: add `point` ops with those ids"
            a, b = expr(c.a, names), expr(c.b, names)
            return None if is_zero(a - b) else f"{c.a} is {sp.N(a, 6)}, not {sp.N(b, 6)}"
        if c.kind in ("equal", "value"):
            a, b = expr(c.a, names), expr(c.b, names)
            return None if is_zero(a - b) else f"{c.a} ≠ {c.b} (difference {sp.simplify(a - b)})"
        if c.kind == "equivalent":
            a, b = relation(c.a, names), relation(c.b, names)
            if is_zero(a - b) or (not is_zero(b) and not sp.simplify(a / b).has(x)
                                  and not is_zero(sp.simplify(a / b))):  # fmt: skip
                return None
            sa, sb = _solutions(a, x), _solutions(b, x)
            if sa is not None and sa == sb:
                return None
            return f"{c.a} and {c.b} don't have the same solutions ({sa} vs {sb})"
        if c.kind == "solves":
            e = relation(c.a, names)
            vals = [expr(v, names) for v in c.values]
            bad = [str(v) for v in vals if not is_zero(e.subs(x, v))]
            if bad:
                return f"{', '.join(bad)} doesn't satisfy {c.a}"
            sols = _solutions(e, x)
            if sols is not None and len(sols) != len({sp.nsimplify(v) for v in vals}):
                return f"{c.a} has solutions {sorted(map(str, sols))}, not {c.values}"
            return None
        if c.kind == "derivative":
            a, b = expr(c.a, names), expr(c.b, names)
            d = sp.diff(a, x)
            return None if is_zero(d - b) else f"d/d{x} {c.a} is {sp.simplify(d)}, not {c.b}"
        if c.kind == "antiderivative":
            a, b = expr(c.a, names), expr(c.b, names)
            d = sp.diff(b, x)
            return None if is_zero(d - a) else f"d/d{x} of {c.b} is {sp.simplify(d)}, not {c.a}"
        if c.kind == "definite":
            a, b = expr(c.a, names), expr(c.b, names)
            lo, hi = expr(c.lower, names), expr(c.upper, names)
            v = sp.Integral(a, (x, lo, hi)).evalf(30)
            return None if is_zero(v - b) else f"the integral is {sp.N(v, 8)}, not {c.b}"
        if c.kind == "limit":
            a, b = expr(c.a, names), expr(c.b, names)
            v = sp.limit(a, x, expr(c.lower, names))
            return None if is_zero(v - b) else f"the limit is {v}, not {c.b}"
    except MathError as exc:
        return str(exc)
    except Exception as exc:  # noqa: BLE001 - SymPy can fail in many ways on odd input
        return f"couldn't check: {type(exc).__name__}: {exc}"[:300]
    return f"unknown check {c.kind!r}"


class _Slow(Exception):
    pass


def _alarm(*_: object) -> None:
    raise _Slow


def run(plan: BoardPlan, per_check: int = 0) -> tuple[int, list[str]]:
    """(number of checks, failures as 'step N check K (why): what is wrong').
    `per_check` seconds bounds each check where SIGALRM exists (Unix); elsewhere the
    calling process's own timeout is the bound."""
    n, bad = 0, []
    alarm = bool(per_check) and hasattr(signal, "SIGALRM")
    if alarm:
        signal.signal(signal.SIGALRM, _alarm)
    for si, step in enumerate(plan.steps, 1):
        for c in step.checks:
            n += 1
            if alarm:
                signal.alarm(per_check)
            try:
                err = check(c, plan)
            except _Slow:
                err = f"took longer than {per_check} s to verify; state it more simply"
            finally:
                if alarm:
                    signal.alarm(0)
            if err:
                bad.append(f"step {si} check {c.kind} ({c.why or c.a}): {err}")
    return n, bad


def main() -> None:
    """`python -m app.board.checks < plan.json`: run in its own process so a check that
    makes SymPy hang can be stopped."""
    n, bad = run(BoardPlan.model_validate_json(sys.stdin.read()), per_check=20)
    print(json.dumps({"checks": n, "failed": bad}))


if __name__ == "__main__":
    main()
