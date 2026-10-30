"""Manim scene that plays a board plan on its timeline. Run through `app.board.engine`:

    BOARD_JOB=<job dir> BOARD_PART=<i>/<n> manim render scene.py BoardScene

Everything is laid out first (`Builder`), so any part of the video can be rendered on
its own: earlier operations are added in their finished state and the camera starts
where it left off. That is what lets the parts render in parallel."""

import json
import math
import os
import re
import textwrap
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import manim as m
import numpy as np
import sympy as sp

from app.board import layout as L
from app.board.contracts import BoardPlan, Op
from app.board.mathexpr import expr as parse
from app.board.mathexpr import function
from app.board.timeline import FPS, captions

if tex := os.environ.get("BOARD_TEX"):
    m.config.tex_dir = tex
if media := os.environ.get("BOARD_MEDIA"):
    m.config.media_dir = media

INK, MUTED, PAPER, GRID = "#1f2430", "#5b6372", "#fbfaf6", "#e7eaf0"
COLORS = {"ink": INK, "blue": "#1f5fbf", "red": "#c8323c", "green": "#1d8348",
          "orange": "#d9731a", "purple": "#7a3fb5"}  # fmt: skip
FONT = "DejaVu Sans"
TEMPLATE = m.TexTemplate(preamble=r"\usepackage{amsmath}\usepackage{amssymb}")


@dataclass
class Item:
    mobs: list[m.Mobject]  # on the board once the op is done
    anim: Callable[[], m.Animation] | None
    focus: L.Box
    cam: float = 0
    after: Callable[[], None] | None = None  # finished state of a change, e.g. a dimmed line


@dataclass
class Figure:
    ax: m.Axes
    box: L.Box
    axes: bool
    pts: dict[str, np.ndarray] = field(default_factory=dict)
    taken: list[L.Box] = field(default_factory=list)  # labels and dots already placed


def _box(mob: m.Mobject) -> L.Box:
    return L.Box(mob.get_left()[0], mob.get_top()[1], mob.get_right()[0], mob.get_bottom()[1])


def _union(*bs: L.Box) -> L.Box:
    return L.Box(min(b.x0 for b in bs), max(b.top for b in bs),
                 max(b.x1 for b in bs), min(b.bottom for b in bs))  # fmt: skip


def _overlap(a: L.Box, b: L.Box) -> bool:
    return a.x0 < b.x1 and b.x0 < a.x1 and a.bottom < b.top and b.bottom < a.top


def _step(span: float) -> float:
    raw = span / 6
    mag = 10 ** math.floor(math.log10(raw))
    return next(k * mag for k in (1, 2, 5, 10) if k * mag >= raw)


def _tex_error(exc: Exception) -> str:
    msg = str(exc)
    if hit := re.search(r"log file:\s*(\S+)", msg):
        try:
            lines = Path(hit[1]).read_text(errors="replace").splitlines()
            bang = [ln for ln in lines if ln.startswith("!")]
            if bang:
                return "LaTeX: " + "; ".join(bang[:2])
        except OSError:
            pass
    return f"{type(exc).__name__}: {msg}"[:300]


def text(s: str, width: float, size: int = 28, color: str = MUTED, bold: bool = False) -> m.Text:
    weight = m.BOLD if bold else m.NORMAL
    t = m.Text(s, font=FONT, font_size=size, color=color, weight=weight)
    if t.width > width:
        chars = max(12, int(len(s) * width / t.width) - 1)
        t = m.Text(textwrap.fill(s, chars), font=FONT, font_size=size, color=color,
                   weight=weight, line_spacing=0.8)  # fmt: skip
    return t


class Builder:
    """Lays out and builds every op of a plan, without playing anything."""

    def __init__(self, plan: BoardPlan):
        self.plan = plan
        self.board = L.Board()
        self.objs: dict[str, m.Mobject] = {}
        self.rows: list[m.Mobject] = []  # written rows, so arrows can go around them
        self.fig: Figure | None = None
        self.errors: list[dict] = []

    def title(self) -> Item:
        w = L.RIGHT - L.LEFT
        head = text(self.plan.title, w, 40, INK, bold=True)
        sub = text(self.plan.problem, w, 26)
        g = m.VGroup(head, sub).arrange(m.DOWN, aligned_edge=m.LEFT, buff=0.18)
        b = self.board.row(g.width, g.height)
        g.move_to([*b.center, 0])
        rule = m.Line([L.LEFT, b.bottom - 0.12, 0], [L.RIGHT, b.bottom - 0.12, 0],
                      color=GRID, stroke_width=3)  # fmt: skip
        self.board.y -= 0.2
        return Item(
            [g, rule],
            lambda: m.AnimationGroup(m.Write(head), m.FadeIn(sub), m.Create(rule), lag_ratio=0.3),
            b,
        )

    def all(self, strict: bool = True) -> list[list[Item]]:
        out = []
        for si, step in enumerate(self.plan.steps):
            row = []
            for oi, op in enumerate(step.ops):
                try:
                    it = self.op(op)
                except Exception as exc:  # noqa: BLE001 - reported per op for repair
                    if strict:
                        raise
                    self.errors.append({"step": si + 1, "op": oi + 1, "kind": op.kind,
                                        "latex": op.latex, "error": _tex_error(exc)})  # fmt: skip
                    it = Item([], None, L.Box(0, self.board.y, 0, self.board.y))
                it.cam = self.board.follow(it.focus)
                if op.id and it.mobs:
                    self.objs[op.id] = it.mobs[0]
                if op.kind in ("write", "text", "section") and it.mobs:
                    self.rows.append(it.mobs[0])
                row.append(it)
            out.append(row)
        return out

    # -- rows ---------------------------------------------------------------------------
    def _row(self, mob: m.Mobject, max_h: float = 2.2) -> L.Box:
        w = self.board.width()
        if mob.width > w:
            mob.scale_to_fit_width(w)
        if mob.height > max_h:
            mob.scale_to_fit_height(max_h)
        b = self.board.row(mob.width, mob.height)
        mob.move_to([*b.center, 0])
        return b

    def op(self, op: Op) -> Item:
        c = COLORS[op.color]
        if op.kind == "write":
            t = m.MathTex(op.latex, tex_template=TEMPLATE, color=c, font_size=46)
            b = self._row(t)
            src = self.objs.get(op.target)
            if src is not None:  # flag the line it comes from, never draw over it
                return Item([t], lambda: m.AnimationGroup(
                    m.Indicate(src, color=COLORS["blue"], scale_factor=1.04), m.Write(t),
                    lag_ratio=0.3), b)  # fmt: skip
            return Item([t], lambda: _pen(m.Write(t), t), b)
        if op.kind == "text":
            t = text(op.text, self.board.width(), 28, c if op.color != "ink" else MUTED)
            return Item([t], lambda: m.Write(t), self._row(t))
        if op.kind == "section":
            t = text(op.latex or op.text, L.RIGHT - L.LEFT, 32, COLORS["blue"], bold=True)
            b = self.board.section(t.height + 0.15)
            t.move_to([L.LEFT + t.width / 2, b.top - t.height / 2, 0])
            rule = m.Line([L.LEFT, b.bottom, 0], [L.RIGHT, b.bottom, 0], color=COLORS["blue"],
                          stroke_width=2, stroke_opacity=0.5)  # fmt: skip
            return Item([t, rule], lambda: m.AnimationGroup(m.Write(t), m.Create(rule)), b)
        if op.kind == "quote":
            body = text(f"“{op.text}”", self.board.width() - 0.8, 28, INK)
            fill = c if op.color != "ink" else COLORS["purple"]
            bar = m.Rectangle(
                width=0.08,
                height=body.height + 0.3,
                fill_color=fill,
                fill_opacity=1,
                stroke_width=0,
            )
            g = m.VGroup(bar, body).arrange(m.RIGHT, buff=0.3)
            return Item([g], lambda: m.AnimationGroup(m.GrowFromEdge(bar, m.UP), m.Write(body),
                                                      lag_ratio=0.3), self._row(g))  # fmt: skip
        if op.kind == "bars":
            return self.bars(op, c)
        if op.kind == "formula":
            return self.formula(op, c)
        if op.kind in ("box", "underline", "cross", "arrow"):
            return self.mark(op, c)
        if op.kind == "figure":
            return self.figure(op)
        assert self.fig is not None, "draw a figure first"
        return self.draw(op, c, self.fig)

    def bars(self, op: Op, c: str) -> Item:
        """A small bar chart: the source's own numbers, each bar labelled with its value."""
        top = max(abs(v) for v in op.values) or 1
        neg = any(v < 0 for v in op.values)
        palette = [COLORS[k] for k in ("blue", "purple", "green", "orange", "red")]
        cols = []
        for i, (lab, v) in enumerate(zip(op.labels, op.values, strict=True)):
            h = max(0.08, (1.3 if neg else 2.2) * abs(v) / top)
            bar = m.Rectangle(width=0.9, height=h, fill_color=palette[i % 5], fill_opacity=0.85,
                              stroke_width=0)  # fmt: skip
            val = text(f"{v:g}", 1.6, 24, INK, bold=True)
            name = text(lab, 1.8, 20, MUTED)
            col = m.VGroup(val, bar, name).arrange(m.DOWN, buff=0.12)
            col.shift(m.DOWN * (col[1].get_bottom()[1] - col.get_bottom()[1]))
            cols.append(m.VGroup(val, bar, name))
        chart = m.VGroup(*cols).arrange(m.RIGHT, buff=0.5, aligned_edge=m.DOWN)
        base = chart.get_bottom()[1] + max(cl[2].height for cl in cols) + 0.12
        if neg:  # a zero line with negative bars hanging below it
            base += 1.3 + 0.4
        for col, v in zip(cols, op.values, strict=True):
            bar, val, name = col[1], col[0], col[2]
            if v < 0:
                bar.move_to([bar.get_x(), base - bar.height / 2, 0])
                val.next_to(bar, m.DOWN, buff=0.1)
                name.move_to([bar.get_x(), base + 0.12 + name.height / 2, 0])
            else:
                bar.move_to([bar.get_x(), base + bar.height / 2, 0])
                val.next_to(bar, m.UP, buff=0.1)
                if neg:
                    name.move_to([bar.get_x(), base - 0.12 - name.height / 2, 0])
                else:
                    name.next_to(bar, m.DOWN, buff=0.12)
        if neg:
            chart.add(
                m.Line(
                    [chart.get_left()[0] - 0.2, base, 0],
                    [chart.get_right()[0] + 0.2, base, 0],
                    color=MUTED,
                    stroke_width=2,
                )
            )
        head = text(op.text, self.board.width(), 26, COLORS["blue"], bold=True)
        g = m.VGroup(head, chart).arrange(m.DOWN, aligned_edge=m.LEFT, buff=0.25)
        b = self._row(g, max_h=3.6)
        return Item([g], lambda: m.AnimationGroup(
            m.Write(head), *(m.AnimationGroup(m.GrowFromEdge(cl[1], m.DOWN), m.FadeIn(cl[0]),
                                              m.FadeIn(cl[2])) for cl in cols),
            lag_ratio=0.25), b)  # fmt: skip

    def formula(self, op: Op, c: str) -> Item:
        """The rule in use, in a card on the right; the working continues on the left."""
        edge = COLORS["blue"] if op.color == "ink" else c
        tex = m.MathTex(op.latex, tex_template=TEMPLATE, color=INK, font_size=40)
        parts: list[m.Mobject] = [tex]
        if op.text:
            parts.insert(0, text(op.text, L.FIG_W - 0.5, 22, edge, bold=True))
        body = m.VGroup(*parts).arrange(m.DOWN, buff=0.18)
        if body.width > L.FIG_W - 0.5:
            body.scale_to_fit_width(L.FIG_W - 0.5)
        card = m.RoundedRectangle(corner_radius=0.15, width=body.width + 0.5,
                                  height=body.height + 0.4, stroke_color=edge, stroke_width=3,
                                  fill_color="#ffffff", fill_opacity=0.9)  # fmt: skip
        b = self.board.figure(card.width, card.height)
        g = m.VGroup(card, body.move_to(card)).move_to([*b.center, 0])
        return Item([g], lambda: m.AnimationGroup(m.Create(card), *(m.Write(x) for x in parts),
                                                  lag_ratio=0.25), b)  # fmt: skip

    def mark(self, op: Op, c: str) -> Item:
        t = self.objs[op.target]
        red = COLORS["red"] if op.color == "ink" else c
        if op.kind == "box":
            r = m.SurroundingRectangle(t, color=red, buff=0.12, corner_radius=0.08,
                                       stroke_width=4)  # fmt: skip
            return Item([r], lambda: m.Create(r), _box(r))
        if op.kind == "underline":
            u = m.Underline(t, color=red, buff=0.08, stroke_width=4)
            return Item([u], lambda: m.Create(u), _box(t))
        if op.kind == "cross":
            x = m.Line(t.get_corner(m.DL), t.get_corner(m.UR), color=red, stroke_width=5)

            def dim() -> None:
                t.set_opacity(0.4)

            return Item([x], lambda: m.AnimationGroup(m.Create(x), t.animate.set_opacity(0.4)),
                        _box(t), after=dim)  # fmt: skip
        to = self.objs[op.to]
        hi, lo = max(t.get_top()[1], to.get_top()[1]), min(t.get_bottom()[1], to.get_bottom()[1])
        between = [r for r in self.rows if r.get_bottom()[1] < hi and r.get_top()[1] > lo]
        xr = max(r.get_right()[0] for r in [t, to, *between]) + 0.25
        p1, p2 = np.array([xr, t.get_center()[1], 0]), np.array([xr, to.get_center()[1], 0])
        if abs(p1[1] - p2[1]) > 2.5:  # far apart (e.g. a figure between): a short arrow in
            p1 = p2 + (m.UP if p1[1] > p2[1] else m.DOWN) * 1.0
        a = m.CurvedArrow(p1, p2, angle=-m.PI / 2 if p2[1] < p1[1] else m.PI / 2, color=red,
                          stroke_width=3, tip_length=0.18)  # fmt: skip
        mobs: list[m.Mobject] = [a]
        if op.text:
            lab = m.Text(op.text, font=FONT, font_size=24, color=red)
            lab.next_to(a, m.RIGHT, buff=0.1)
            mobs.append(lab)
        g = m.VGroup(*mobs)
        return Item(mobs, lambda: m.Create(g), _union(_box(t), _box(to), _box(g)))

    # -- figures ------------------------------------------------------------------------
    def figure(self, op: Op) -> Item:
        (x0, x1), (y0, y1) = op.x_range, op.y_range
        if op.axes:
            w, h = L.FIG_W, L.FIG_H
        else:  # a wide blank plane (x span 8+) is a full-width diagram
            fw = L.RIGHT - L.LEFT if x1 - x0 >= 8 else L.FIG_W
            unit = min(fw / (x1 - x0), L.FIG_H / (y1 - y0))
            w, h = unit * (x1 - x0), unit * (y1 - y0)
        if not op.axes and x1 - x0 >= 8:  # a diagram: its own full-width block of rows
            r = self.board.row(w, h)
            off = (L.RIGHT - L.LEFT - w) / 2
            b = L.Box(r.x0 + off, r.top, r.x1 + off, r.bottom)
        else:
            b = self.board.figure(w, h)
        dx, dy = _step(x1 - x0), _step(y1 - y0)
        ax = m.Axes(
            x_range=[x0, x1, dx], y_range=[y0, y1, dy], x_length=w, y_length=h, tips=False,
            axis_config={"color": INK, "stroke_width": 2, "include_numbers": op.axes,
                         "include_ticks": op.axes, "font_size": 22,
                         "decimal_number_config": {"num_decimal_places": 0 if dx >= 1 else 1,
                                                   "color": MUTED}},
        )  # fmt: skip
        ax.move_to([*b.center, 0])
        self.fig = Figure(ax, b, op.axes)
        if op.axes:  # labels keep clear of the axis numbers
            self.fig.taken += [_box(n) for n in (*ax.x_axis.numbers, *ax.y_axis.numbers)]
        if not op.axes:
            ax.set_opacity(0)
            return Item([], None, b)
        return Item([ax], lambda: m.Create(ax), b)

    def _place(self, lab: m.Mobject, at: np.ndarray, dirs: list[np.ndarray]) -> None:
        """Put a label next to `at` on the first side that is inside the figure and clear
        of the labels and dots already there."""
        fig = self.fig
        assert fig is not None
        room = L.Box(fig.box.x0 - 0.5, fig.box.top + 0.5, fig.box.x1 + 0.3, fig.box.bottom - 0.5)
        for d in [*dirs, m.UR, m.UL, m.DR, m.DL, m.UP, m.DOWN, m.RIGHT, m.LEFT]:
            lab.next_to(at, d, buff=0.14)
            b = _box(lab)
            inside = room.x0 <= b.x0 and b.x1 <= room.x1 and room.bottom <= b.bottom <= room.top
            if inside and not any(_overlap(b, t) for t in fig.taken):
                break
        else:
            lab.next_to(at, dirs[0] if dirs else m.UR, buff=0.14)
        fig.taken.append(_box(lab))

    def _curve(self, ax: m.Axes, f: Callable, a: float, b: float) -> list[np.ndarray]:
        (y0, y1) = ax.y_range[:2]
        xs = np.linspace(a, b, 400)
        with np.errstate(all="ignore"):
            ys = np.asarray(f(xs), dtype=float) * np.ones_like(xs)
        pad = (y1 - y0) * 0.02
        runs, cur = [], []
        for x, y in zip(xs, ys, strict=True):
            if np.isfinite(y) and y0 - pad <= y <= y1 + pad:
                cur.append(ax.c2p(x, y))
            elif cur:
                runs.append(cur)
                cur = []
        return [*runs, cur] if cur else runs

    def draw(self, op: Op, c: str, fig: Figure) -> Item:
        ax, b = fig.ax, fig.box
        if op.kind in ("plot", "area", "riemann", "tangent"):
            f = function(op.expr)
        if op.kind == "plot":
            g = m.VGroup(*(m.VMobject(color=c if op.color != "ink" else COLORS["blue"],
                                      stroke_width=4).set_points_as_corners(r)
                           for r in self._curve(ax, f, *ax.x_range[:2]) if len(r) > 1))  # fmt: skip
            mobs: list[m.Mobject] = [g]
            if op.latex and len(g):
                lab = m.MathTex(op.latex, tex_template=TEMPLATE, font_size=30, color=g[0].color)
                self._place(lab, g[-1].get_end(), [m.UL, m.LEFT, m.DL, m.DOWN])
                mobs.append(lab)
            return Item(mobs, lambda: m.AnimationGroup(*(m.Create(x) for x in mobs)), b)
        if op.kind == "area":
            y0, y1 = ax.y_range[:2]
            xs = np.linspace(op.a, op.b, 160)
            with np.errstate(all="ignore"):
                ys = np.clip(np.asarray(f(xs), dtype=float) * np.ones_like(xs), y0, y1)
            base = min(max(0, y0), y1)
            pts = [ax.c2p(op.a, base), *(ax.c2p(x, y) for x, y in zip(xs, ys, strict=True)),
                   ax.c2p(op.b, base)]  # fmt: skip
            col = c if op.color != "ink" else COLORS["blue"]
            area = m.Polygon(*pts, stroke_width=0, fill_color=col, fill_opacity=0.3)
            return Item([area], lambda: m.FadeIn(area), b)
        if op.kind == "riemann":
            dx = (op.b - op.a) / op.n
            how = {"left": 0, "right": 1}.get(op.text.strip().lower(), 0.5)
            rects = []
            for i in range(op.n):
                xl = op.a + i * dx
                with np.errstate(all="ignore"):
                    h = float(f(xl + how * dx))
                h = h if np.isfinite(h) else 0
                col = COLORS["green"] if i % 2 else COLORS["blue"]
                rects.append(m.Polygon(ax.c2p(xl, 0), ax.c2p(xl, h), ax.c2p(xl + dx, h),
                                       ax.c2p(xl + dx, 0), stroke_width=1.5, stroke_color=INK,
                                       fill_color=col, fill_opacity=0.35))  # fmt: skip
            g = m.VGroup(*rects)
            return Item([g], lambda: m.LaggedStart(*(m.FadeIn(r) for r in rects),
                                                   lag_ratio=0.15), b)  # fmt: skip
        if op.kind == "tangent":
            x = sp.Symbol("x")
            e = parse(op.expr, {"x": x})
            y0, k = float(e.subs(x, op.at)), float(sp.diff(e, x).subs(x, op.at))
            d = (ax.x_range[1] - ax.x_range[0]) * 0.2
            col = c if op.color != "ink" else COLORS["orange"]
            line = m.Line(ax.c2p(op.at - d, y0 - k * d), ax.c2p(op.at + d, y0 + k * d),
                          color=col, stroke_width=3)  # fmt: skip
            dot = m.Dot(ax.c2p(op.at, y0), radius=0.06, color=col)
            return Item([line, dot], lambda: m.AnimationGroup(m.Create(line), m.FadeIn(dot)), b)
        if op.kind == "node":
            edge = COLORS["blue"] if op.color == "ink" else c
            lab = text(op.text, 2.6, 22, INK, bold=True)
            card = m.RoundedRectangle(corner_radius=0.14, width=lab.width + 0.4,
                                      height=lab.height + 0.32, stroke_color=edge,
                                      stroke_width=3, fill_color=edge,
                                      fill_opacity=0.12)  # fmt: skip
            g = m.VGroup(card, lab.move_to(card)).move_to(fig.ax.c2p(op.x, op.y))
            fig.pts[op.id] = g.get_center()
            fig.taken.append(_box(g))
            if fig.box.x1 - fig.box.x0 > L.FIG_W:  # a full-width diagram: rows go below its cards
                self.board.y = min(self.board.y, _box(g).bottom - L.GAP)
            return Item([g], lambda: m.AnimationGroup(m.Create(card), m.FadeIn(lab)), _box(g))
        if op.kind == "link":
            a, z = (self.objs[n] for n in op.points)
            edge = COLORS["ink"] if op.color == "ink" else c
            arr = m.Arrow(a, z, buff=0.08, color=edge, stroke_width=4, tip_length=0.2)
            mobs: list[m.Mobject] = [arr]
            if op.text:
                t = text(op.text, 2.2, 18, MUTED)
                if t.width > (room := max(arr.get_length() - 0.1, 0.8)):
                    t.scale_to_fit_width(room)  # a short arrow: keep the label off the cards
                for side in (m.UP, m.DOWN):
                    t.next_to(arr.get_center(), side, buff=0.08)
                    if not any(_overlap(_box(t), k) for k in fig.taken):
                        break
                fig.taken.append(_box(t))
                mobs.append(t)
            return Item(mobs, lambda: m.AnimationGroup(*(m.GrowArrow(x) if x is arr else
                                                         m.FadeIn(x) for x in mobs)),
                        _box(m.VGroup(*mobs)))  # fmt: skip
        if op.kind == "point":
            p = ax.c2p(op.x, op.y)
            name = op.latex or op.id
            fig.pts.update({n: p for n in (op.id, op.latex) if n})
            dot = m.Dot(p, radius=0.065, color=c)
            out = p - np.array([*b.center, 0])
            out = out / (np.linalg.norm(out) or 1) if np.linalg.norm(out) > 0.2 else m.UR
            lab = m.MathTex(name, tex_template=TEMPLATE, font_size=34, color=c)
            fig.taken.append(_box(dot))
            # On a graph, label above the point so it never reads as an axis number.
            dirs = ([m.UL, m.UR, m.UP] if fig.axes else
                    [d for d in (m.UR, m.UL, m.DR, m.DL) if np.dot(d, out) > 0])  # fmt: skip
            self._place(lab, p, dirs)
            return Item([dot, lab], lambda: m.AnimationGroup(m.GrowFromCenter(dot),
                                                             m.Write(lab)), b)  # fmt: skip
        pts = [fig.pts[n] for n in op.points]
        if op.kind == "segment":
            ln = m.Line(pts[0], pts[1], color=c, stroke_width=4)
            mobs = [ln]
            if op.latex:
                mid = (pts[0] + pts[1]) / 2
                out = mid - np.array([*b.center, 0])
                nrm = np.array([-(pts[1] - pts[0])[1], (pts[1] - pts[0])[0], 0])
                nrm = nrm / (np.linalg.norm(nrm) or 1)
                nrm = nrm if np.dot(nrm, out) >= 0 else -nrm
                lab = m.MathTex(op.latex, tex_template=TEMPLATE, font_size=30, color=c)
                lab.move_to(mid + nrm * 0.3)
                mobs.append(lab)
            return Item(mobs, lambda: m.AnimationGroup(*(m.Create(x) for x in mobs)), b)
        if op.kind == "polygon":
            poly = m.Polygon(*pts, color=c, stroke_width=4, fill_color=c, fill_opacity=0.06)
            return Item([poly], lambda: m.Create(poly), b)
        if op.kind == "circle":
            r = (np.linalg.norm(pts[1] - pts[0]) if len(pts) > 1
                 else op.radius * ax.x_axis.unit_size)  # fmt: skip
            circ = m.Circle(radius=r, color=c, stroke_width=4).move_to(pts[0])
            return Item([circ], lambda: m.Create(circ), b)
        if op.kind == "angle":
            a, v, cc = pts
            u, w = a - v, cc - v
            ccw = math.atan2(u[0] * w[1] - u[1] * w[0], float(np.dot(u, w))) % math.tau
            deg = math.degrees(min(ccw, math.tau - ccw))
            l1, l2 = m.Line(v, a), m.Line(v, cc)
            other = ccw > math.pi
            if abs(deg - 90) < 0.5:
                mark = m.RightAngle(l1, l2, length=0.28, color=c, stroke_width=3)
            else:
                mark = m.Angle(l1, l2, radius=0.45, other_angle=other, color=c, stroke_width=3)
            mobs = [mark]
            if op.latex:
                bis = u / np.linalg.norm(u) + w / np.linalg.norm(w)
                bis = bis / (np.linalg.norm(bis) or 1)
                lab = m.MathTex(op.latex, tex_template=TEMPLATE, font_size=28, color=c)
                lab.move_to(v + bis * 0.8)
                mobs.append(lab)
            return Item(mobs, lambda: m.AnimationGroup(*(m.Create(x) for x in mobs)), b)
        if op.kind == "label":
            p = fig.pts[op.points[0]] if op.points else ax.c2p(op.x, op.y)
            lab = m.MathTex(op.latex, tex_template=TEMPLATE, font_size=30, color=c)
            self._place(lab, p, [m.UR, m.UL])
            return Item([lab], lambda: m.Write(lab), b)
        raise ValueError(f"unknown op {op.kind}")


def _pen(write: m.Animation, t: m.Mobject) -> m.Animation:
    """A pen tip that moves along the line while it is written, like a teacher's marker."""
    pen = m.VGroup(
        m.Dot(radius=0.075, color=COLORS["orange"]),
        m.Line([0, 0, 0], [0.32, 0.5, 0], color=COLORS["orange"], stroke_width=6),
    )
    pen.set_opacity(0)
    x0, x1, y = t.get_left()[0], t.get_right()[0], t.get_bottom()[1] + 0.05

    def follow(mob: m.Mobject, a: float) -> None:
        mob.move_to([x0 + (x1 - x0) * a + 0.15, y + 0.25, 0])
        mob.set_opacity(0 if a >= 0.99 else 1)

    return m.AnimationGroup(write, m.UpdateFromAlphaFunc(pen, follow))


def _grid(top: float, bottom: float) -> m.VGroup:
    s = 0.5
    ys = np.arange(math.ceil(top / s) * s, bottom - s, -s)
    xs = np.arange(-7.5, 7.51, s)
    lines = [m.Line([-7.6, y, 0], [7.6, y, 0]) for y in ys]
    lines += [m.Line([x, ys[0], 0], [x, ys[-1], 0]) for x in xs]
    return m.VGroup(*lines).set_stroke(GRID, 1.2)


CAPTION_W = 9.4  # leaves the step chip its corner of the caption band


def caption_card(s: str) -> m.VGroup:
    t = m.Text(s, font=FONT, font_size=26, color="#ffffff")
    if t.width > CAPTION_W:
        t.scale_to_fit_width(CAPTION_W)
    bg = m.RoundedRectangle(corner_radius=0.14, width=t.width + 0.5, height=t.height + 0.3,
                            fill_color=INK, fill_opacity=0.82, stroke_width=0)  # fmt: skip
    return m.VGroup(bg, t.move_to(bg))


def _chip(s: str) -> m.VGroup:
    t = m.Text(s, font=FONT, font_size=20, color=MUTED)
    bg = m.RoundedRectangle(corner_radius=0.1, width=t.width + 0.3, height=t.height + 0.2,
                            fill_color=PAPER, fill_opacity=0.92, stroke_width=0)  # fmt: skip
    return m.VGroup(bg, t.move_to(bg))


class BoardScene(m.MovingCameraScene):
    def construct(self) -> None:
        job = Path(os.environ["BOARD_JOB"])
        plan = BoardPlan.model_validate_json((job / "plan.json").read_text())
        tl = json.loads((job / "timeline.json").read_text())
        part, parts = (int(v) for v in os.environ.get("BOARD_PART", "0/1").split("/"))
        self.camera.background_color = PAPER
        frame = self.camera.frame

        b = Builder(plan)
        title = b.title()
        items = b.all()
        events = tl["events"]
        starts = [round(s["start"] * FPS) for s in tl["steps"]]
        cuts = [0, *(starts[round(i * len(starts) / parts)] for i in range(1, parts))]
        cuts.append(tl["frames"])
        f0, f1 = cuts[part], cuts[part + 1]
        cam_min = min([it.cam for row in items for it in row] or [0])
        self.add(_grid(4.5, min(b.board.bottom(), cam_min - L.FRAME_H / 2) - 1))

        # Captions and the step chip stay fixed in the 16:9 frame while the board scrolls,
        # both in the caption band, so the chip never covers what is drawn.
        caps = captions(tl)
        cards = [caption_card(s) for _, _, s in caps]
        chips = [_chip(f"Step {i} of {len(starts)}") for i in range(1, len(starts) + 1)]
        clock = {"t": f0 / FPS, "cap": -2, "chip": -2}
        cap, chip = m.VGroup(), m.VGroup()

        def tick(mob: m.Mobject, dt: float) -> None:
            clock["t"] += dt
            t = clock["t"]
            i = next((k for k, (a, z, _) in enumerate(caps) if a <= t < z), -1)
            if i != clock["cap"]:
                cap.become(cards[i].copy() if i >= 0 else m.VGroup())
                clock["cap"] = i
            j = next((k for k in range(len(starts) - 1, -1, -1) if starts[k] / FPS <= t), -1)
            if j != clock["chip"]:
                chip.become(chips[j].copy() if j >= 0 else m.VGroup())
                clock["chip"] = j
            c = frame.get_center()
            if len(cap):
                cap.move_to([c[0], c[1] - L.FRAME_H / 2 + 0.62, 0])
            if len(chip):
                chip.move_to([c[0] + L.FRAME_W / 2 - 0.3 - chip.width / 2,
                              c[1] - L.FRAME_H / 2 + 0.62, 0])  # fmt: skip

        cap.add_updater(tick)
        chip.add_updater(lambda mob, dt: None)

        cam = 0.0
        now = f0
        if part == 0:
            self.add(cap, chip)
            self.play(title.anim(), run_time=1.2)  # type: ignore[misc]
            now += round(1.2 * FPS)
        else:
            self.add(*title.mobs)
        for ev in events:
            if ev["frame"] >= f0:
                continue
            it = items[ev["step"]][ev["op"]]
            self.add(*it.mobs)
            if it.after:
                it.after()
            cam = it.cam
        frame.move_to([0, cam, 0])
        if part:
            self.add(cap, chip)

        def until(f: int) -> None:
            nonlocal now
            if f > now:
                self.wait((f - now) / FPS)
                now = f

        for ev in events:
            if not f0 <= ev["frame"] < f1:
                continue
            until(ev["frame"])
            it = items[ev["step"]][ev["op"]]
            anims = [it.anim()] if it.anim else []
            if abs(it.cam - cam) > 1e-3:
                anims.append(frame.animate.move_to([0, it.cam, 0]))
                cam = it.cam
            if anims:
                self.play(*anims, run_time=ev["frames"] / FPS)
            else:
                self.wait(ev["frames"] / FPS)
            now += ev["frames"]
        until(f1)
