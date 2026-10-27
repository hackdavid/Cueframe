"""Tutorial state graph: each step is an editable state. The DB/JSON keeps only
metadata and URLs; clips, audio and screenshots live in the bucket."""

from typing import Literal

from pydantic import BaseModel, Field


class Word(BaseModel):
    text: str
    t: float  # seconds from the start of this state's narration
    end: float


class Narration(BaseModel):
    text: str
    at: float = 0  # seconds into the state
    audio_url: str | None = None
    duration: float = 0
    words: list[Word] = []


class Camera(BaseModel):
    """Zoom keyframe. `t` is seconds into the state; (cx, cy) is the focus point in
    source pixels; zoom 1 = full frame. The output size never changes."""

    t: float
    zoom: float = 1
    cx: float = 960
    cy: float = 540


class Highlight(BaseModel):
    t_in: float
    t_out: float
    x: int
    y: int
    w: int
    h: int
    label: str | None = None


class Sfx(BaseModel):
    t: float
    kind: Literal["click", "keys", "whoosh", "success", "pop"]
    duration: float | None = None


class Source(BaseModel):
    """Window into the continuous master recording, played at `rate` (time-lapse > 1)."""

    t_in: float
    t_out: float
    rate: float = 1


class State(BaseModel):
    id: str
    kind: Literal["title", "action", "wait", "outro"] = "action"
    app: Literal["browser", "terminal"] | None = None
    label: str = ""
    narration: list[Narration] = []
    source: Source | None = None
    camera: list[Camera] = []
    highlights: list[Highlight] = []
    sfx: list[Sfx] = []
    verified: bool = True
    badge: str | None = None
    duration: float = 0  # filled by the planner: max(clip, narration) + padding


class Tutorial(BaseModel):
    id: str
    title: str
    subtitle: str = ""
    width: int = 1920
    height: int = 1080
    fps: int = 30
    recording_url: str | None = None
    music_url: str | None = None
    steps: list[str] = []  # step labels, for the progress bar
    software: str = ""  # title-card chip
    teaser: str = ""  # command typed on the title card
    done: str = "You did it"  # outro headline
    recap: list[str] = []  # outro checklist
    states: list[State] = Field(default_factory=list)
