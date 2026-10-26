"""Pydantic contracts for sandbox actions and their timestamped records."""

from typing import Literal

from pydantic import BaseModel, Field

ActionKind = Literal[
    "open_url", "click", "double_click", "type", "key", "scroll", "wait", "terminal"
]


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


class Answer(BaseModel):
    input_id: str
    value: str = Field(min_length=1)
