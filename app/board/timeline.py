"""When each board operation starts, from the narration's word timings. Everything is
in whole frames so separately rendered parts join without drift."""

import re
from dataclasses import asdict, dataclass

from app.board.contracts import BoardPlan, Op

FPS = 30
INTRO = 2.0  # title is written before the first line is spoken
GAP = 0.6  # pause after each step
OUTRO = 2.5


def run_time(op: Op) -> float:
    if op.kind == "write":
        return min(2.6, max(0.9, 0.5 + 0.07 * len(op.latex)))
    if op.kind == "text":
        return min(2.0, max(0.7, 0.4 + 0.03 * len(op.text)))
    return {"section": 1.0, "formula": 1.8, "figure": 1.2, "plot": 1.6, "area": 1.0, "riemann": 1.4,
            "tangent": 1.0, "polygon": 1.2, "circle": 1.2}.get(op.kind, 0.7)  # fmt: skip


@dataclass
class Event:
    step: int
    op: int
    frame: int
    frames: int


@dataclass
class StepTime:
    start: float  # seconds into the video
    audio: float  # narration length
    end: float
    words: list[dict]  # {text, t, end} relative to `start`


def _norm(w: str) -> str:
    return re.sub(r"[^a-z0-9]", "", w.lower())


def cues(say_words: list[dict], ops: list[Op]) -> list[float | None]:
    """Seconds into the step where each op's cue word is spoken (in order)."""
    out: list[float | None] = []
    i = 0
    words = [_norm(w["text"]) for w in say_words]
    for op in ops:
        cue = _norm(op.cue)
        hit = next((j for j in range(i, len(words)) if cue and words[j] == cue), None)
        if hit is None and cue:
            hit = next((j for j in range(i, len(words)) if words[j].startswith(cue)), None)
        if hit is None:
            out.append(None)
        else:
            out.append(say_words[hit]["t"])
            i = hit + 1
    return out


def frames(t: float) -> int:
    return round(t * FPS)


def build(plan: BoardPlan, voiced: list[tuple[float, list[dict]]]) -> dict:
    """`voiced[i]` is (narration seconds, word timings) for step i. Returns the timeline
    the scene plays: steps with absolute starts, op events and the total length."""
    t = INTRO
    steps: list[StepTime] = []
    events: list[Event] = []
    for si, (step, (audio, words)) in enumerate(zip(plan.steps, voiced, strict=True)):
        start = frames(t) / FPS
        at = cues(words, step.ops)
        cur = start + 0.15
        planned: list[tuple[float, float]] = []
        for oi, op in enumerate(step.ops):
            s = max(cur, start + at[oi]) if at[oi] is not None else cur
            planned.append((s, run_time(op)))
            cur = s + run_time(op) * 0.6  # the next op may start while this one finishes
        for oi, (s, rt) in enumerate(planned):
            nxt = planned[oi + 1][0] if oi + 1 < len(planned) else s + rt
            rt = max(0.35, min(rt, nxt - s))
            f0 = frames(s)
            if events and f0 < events[-1].frame + events[-1].frames:
                f0 = events[-1].frame + events[-1].frames
            events.append(Event(si, oi, f0, max(1, frames(rt))))
        last = (events[-1].frame + events[-1].frames) / FPS if step.ops else start
        end = max(start + audio, last) + GAP
        steps.append(StepTime(start, audio, frames(end) / FPS, words))
        t = end
    total = frames(t + OUTRO)
    return {"fps": FPS, "frames": total, "seconds": total / FPS,
            "steps": [asdict(s) for s in steps], "events": [asdict(e) for e in events]}  # fmt: skip


def captions(tl: dict, max_words: int = 7) -> list[tuple[float, float, str]]:
    """Short caption lines with absolute times, broken at punctuation or every few words."""
    out: list[tuple[float, float, str]] = []
    for st in tl["steps"]:
        chunk: list[dict] = []
        for i, w in enumerate(st["words"]):
            chunk.append(w)
            last = i == len(st["words"]) - 1
            if last or len(chunk) >= max_words or re.search(r"[.,;:!?]$", w["text"]):
                text = " ".join(c["text"] for c in chunk).strip()
                if text:
                    out.append((st["start"] + chunk[0]["t"], st["start"] + chunk[-1]["end"], text))
                chunk = []
    for i in range(len(out) - 1):  # hold each line until the next one starts (max 1.2 s)
        a, b, s = out[i]
        out[i] = (a, min(out[i + 1][0], b + 1.2), s)
    return out


def srt(caps: list[tuple[float, float, str]]) -> str:
    def ts(t: float) -> str:
        ms = round(t * 1000)
        return f"{ms // 3600000:02}:{ms // 60000 % 60:02}:{ms // 1000 % 60:02},{ms % 1000:03}"

    return "".join(f"{i}\n{ts(a)} --> {ts(b)}\n{s}\n\n" for i, (a, b, s) in enumerate(caps, 1))
