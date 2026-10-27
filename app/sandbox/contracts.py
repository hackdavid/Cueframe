"""Pydantic contracts for sandbox actions and their timestamped records."""

from typing import Literal

from pydantic import BaseModel, Field

ActionKind = Literal[
    "open_url",
    "click",
    "double_click",
    "type",
    "key",
    "scroll",
    "wait",
    "terminal",
    "launch",
    "drag",
    "menu",
]


def live_path(job_id: str) -> str:
    """The live desktop as users see it: proxied through Cueframe behind the login, so
    the provider's own (unauthenticated) address is never sent to a browser."""
    base = f"/api/jobs/{job_id}/live"
    return f"{base}/vnc.html?autoconnect=1&resize=scale&view_only=1&path={base[1:]}/websockify"


class Action(BaseModel):
    kind: ActionKind
    url: str | None = None
    x: int | None = None
    y: int | None = None
    text: str | None = None
    keys: str | None = None
    dy: int = 0
    seconds: float = 0
    command: str | None = None
    timeout: float = 600
    ask: bool = True
    caption: str | None = None
    # Bring this app to the front first; apps fill the screen, so only one is visible.
    # "browser", "terminal" or a desktop app's window class (e.g. "mousepad").
    app: str | None = None
    # click/type/scroll: the element's visible words (button text, label, placeholder),
    # found in the sandbox (DevTools for the browser, AT-SPI for desktop apps).
    target: str | None = None
    # click/drag: where inside the target, as (x, y) fractions of its box (default centre);
    # "canvas" names the app's drawing area. drag: from `at` to `to`. menu: target is a
    # path such as "Layer > Layer Style > Drop Shadow", clicked item by item.
    at: list[float] | None = None
    to: list[float] | None = None
    enter: bool = False  # type: press Return after typing
    clear: bool = False  # terminal: clear the screen (Ctrl+L) before typing


class ExecResult(BaseModel):
    returncode: int
    stdout: str = ""
    stderr: str = ""
    seconds: float = 0


class ActionRecord(BaseModel):
    """One executed action. Times are epoch seconds on the sandbox clock;
    `t_in`/`t_out` are offsets into the continuous recording."""

    index: int
    action: Action
    ok: bool
    returncode: int
    started: float
    ended: float
    settled: float
    t_in: float = 0
    t_out: float = 0
    in_video: bool = True
    output_tail: str = ""
    error: str | None = None
    screenshot_url: str | None = None
    box: list[int] | None = None  # screen pixels (x, y, w, h) of the element acted on
    path: list[int] | None = None  # drag: screen pixels (x1, y1, x2, y2) pressed and released


class Answer(BaseModel):
    input_id: str
    value: str = Field(min_length=1)
