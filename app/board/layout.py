"""Where things go on the board. The board is a column of rows that never ends
downwards; figures sit to the right of the rows written beside them. Units are Manim
units: the 16:9 camera frame is 14.2 x 8 and only ever moves vertically."""

from dataclasses import dataclass

FRAME_W, FRAME_H = 128 / 9, 8.0
LEFT, RIGHT = -6.6, 6.6
SPLIT = 0.7  # right edge of the rows while a figure is beside them
FIG_W, FIG_H = 5.4, 4.6
GAP = 0.3
TOP = 3.55
CAPTION = 1.3  # bottom of the frame kept clear for captions
HEADROOM = 0.35


@dataclass
class Box:
    x0: float
    top: float
    x1: float
    bottom: float

    @property
    def center(self) -> tuple[float, float]:
        return (self.x0 + self.x1) / 2, (self.top + self.bottom) / 2

    @property
    def h(self) -> float:
        return self.top - self.bottom


class Board:
    def __init__(self) -> None:
        self.y = TOP  # top of the next row
        self.fig: Box | None = None
        self.cam = 0.0  # camera centre y

    def beside(self) -> bool:
        return self.fig is not None and self.fig.bottom < self.y - 0.05

    def width(self, indent: float = 0) -> float:
        """Widest a row at the cursor may be."""
        return (SPLIT if self.beside() else RIGHT) - LEFT - indent

    def row(self, w: float, h: float, indent: float = 0) -> Box:
        if self.fig is not None and not self.beside():
            self.fig = None  # rows are below it now: full width again
        b = Box(LEFT + indent, self.y, LEFT + indent + w, self.y - h)
        self.y = b.bottom - GAP
        return b

    def figure(self, w: float, h: float) -> Box:
        top = self.y if not self.beside() else self.fig.bottom - GAP  # type: ignore[union-attr]
        if top < self.y:  # a second figure below a first: rows continue under both
            self.y = top
        self.fig = Box(RIGHT - w, top, RIGHT, top - h)
        return self.fig

    def section(self, h: float) -> Box:
        """A heading across the full width, below everything so far."""
        top = min(self.y, self.fig.bottom - GAP if self.fig else self.y) - GAP
        self.fig = None
        b = Box(LEFT, top, RIGHT, top - h)
        self.y = b.bottom - GAP
        return b

    def bottom(self) -> float:
        return min(self.y, self.fig.bottom if self.fig else self.y)

    def follow(self, b: Box) -> float:
        """Move the camera just enough that `b` is in view above the captions."""
        lo = self.cam - FRAME_H / 2 + CAPTION
        hi = self.cam + FRAME_H / 2 - HEADROOM
        if b.bottom < lo:
            self.cam += b.bottom - lo
        elif b.top > hi:
            self.cam += b.top - hi
        self.cam = min(self.cam, 0.0)
        return self.cam
