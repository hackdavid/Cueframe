"""Parse the plain math expressions a board plan uses (SymPy syntax, e.g. 'x**2*exp(x)').
Plans come from a model, so only arithmetic, names and calls are accepted."""

import re

import sympy as sp
from sympy.parsing.sympy_parser import (
    convert_xor,
    implicit_application,
    implicit_multiplication,
    parse_expr,
    standard_transformations,
)

ALLOWED = re.compile(r"^[0-9A-Za-z_+\-*/^().,=\s]*$")
BANNED = re.compile(r"__|\blambda\b|\bimport\b|[A-Za-z_)\]]\s*\.\s*[A-Za-z_]")
TRANSFORMS = (*standard_transformations, implicit_multiplication, implicit_application, convert_xor)
FUNCS: dict[str, object] = {
    n: getattr(sp, n)
    for n in (
        "sin", "cos", "tan", "cot", "sec", "csc", "asin", "acos", "atan", "sinh", "cosh",
        "tanh", "exp", "log", "sqrt", "Abs", "pi", "E", "oo", "factorial", "binomial",
        "floor", "ceiling", "Rational", "root",
    )
}  # fmt: skip
FUNCS.update(ln=sp.log, abs=sp.Abs, e=sp.E, arcsin=sp.asin, arccos=sp.acos, arctan=sp.atan)
GLOBALS = {n: getattr(sp, n) for n in ("Integer", "Float", "Rational", "Symbol", "Function")}


class MathError(ValueError):
    pass


def expr(s: str, names: dict[str, object] | None = None) -> sp.Expr:
    s = (s or "").strip()
    if not s or len(s) > 400 or not ALLOWED.match(s) or BANNED.search(s) or "=" in s:
        raise MathError(f"not a plain math expression: {s!r}")
    try:
        out = parse_expr(
            s, local_dict={**FUNCS, **(names or {})}, global_dict=dict(GLOBALS),
            transformations=TRANSFORMS,
        )  # fmt: skip
    except Exception as exc:  # SymPy raises many types for bad input
        raise MathError(f"can't read {s!r}: {exc}") from exc
    if not isinstance(out, sp.Basic):
        raise MathError(f"not a math expression: {s!r}")
    return out


def relation(s: str, names: dict[str, object] | None = None) -> sp.Expr:
    """'lhs = rhs' as lhs - rhs (an expression is returned as is)."""
    if s.count("=") > 1:
        raise MathError(f"more than one '=' in {s!r}")
    if "=" in s:
        lhs, rhs = s.split("=")
        return expr(lhs, names) - expr(rhs, names)
    return expr(s, names)


def function(s: str, var: str = "x"):  # noqa: ANN201 - numpy callable
    """A numeric f(x) for plotting."""
    x = sp.Symbol(var)
    e = expr(s, {var: x})
    extra = e.free_symbols - {x}
    if extra:
        raise MathError(f"{s!r} uses {', '.join(sorted(map(str, extra)))} besides {var}")
    return sp.lambdify(x, e, "numpy")
