"""Turn a captured run (continuous recording + timestamped action records) and the
narration into the tutorial state graph: per-state footage windows, camera moves,
highlights, sound effects and captions. Pure functions, so edits to one state can
be re-planned and re-rendered without touching the others."""

import json
import re
from pathlib import Path
from typing import Any

from app.tutorial.contracts import Camera, Highlight, Narration, Sfx, Source, State, Tutorial, Word

W, H = 1920, 1080
# Terminal font metrics at CUEFRAME_SANDBOX_TERM_FONT=22 on a 1920x1080 screen.
CHAR_W, LINE_H, TOP = 18.3, 36.7, 22
PROMPT = len("user@cueframe:/work$ ")
COLS = int(W // CHAR_W)
TYPE_S = 0.035  # xdotool per-character delay
SPOKEN = {"engine-x": "nginx"}  # spelled for the voice, shown as written


def caption_words(words: list[Word]) -> list[Word]:
    out: list[Word] = []
    for w in words:
        core = w.text.rstrip(".,!?")
        tail = w.text[len(core) :]
        if core.lower() in SPOKEN:
            w = w.model_copy(update={"text": SPOKEN[core.lower()] + tail})
        if out and out[-1].text.lower() == "two" and core.lower() == "hundred":
            out[-1] = out[-1].model_copy(update={"text": "200" + tail, "end": w.end})
            continue
        out.append(w)
    return out


def narration(audio: Path, key: str, text: str, at: float, base: str) -> Narration:
    words = [Word(**w) for w in json.loads((audio / f"{key}.json").read_text())]
    dur = words[-1].end if words else 0
    return Narration(
        text=text,
        at=at,
        audio_url=f"{base}/audio/{key}.mp3",
        duration=dur,
        words=caption_words(words),
    )


def _cmd_box(cmd: str) -> tuple[int, int]:
    lines = -(-(PROMPT + len(cmd)) // COLS)
    width = min(W - 8, (PROMPT + len(cmd)) * CHAR_W + 16) if lines == 1 else W - 8
    return int(width), int(lines * LINE_H + 10)


def plan_step(
    step: dict[str, Any], rec: dict[str, Any], narr: list[Narration], prev_app: str | None
) -> State:
    a = step["action"]
    app = a.get("app") or ("terminal" if a["kind"] == "terminal" else "browser")
    run = rec["ended"] - rec["started"]
    lead = 0.25
    switched = prev_app is not None and prev_app != app
    t = lead + (0.35 if switched else 0.1)
    for n in narr:
        n.at = t
        t = n.at + n.duration + 0.35
    if len(narr) > 1:  # the follow-up line waits for the command to finish
        narr[1].at = max(narr[1].at, lead + run + 0.3)
    end = max(n.at + n.duration for n in narr) if narr else run
    window = rec["t_out"] - (rec["t_in"] - lead)
    dur = round(min(window, max(end + 1.0, lead + run + 1.6, 3.4)), 2)
    st = State(
        id=step["id"],
        app=app,
        label=step["label"],
        narration=narr,
        verified=rec["ok"],
        source=Source(t_in=max(0, rec["t_in"] - lead), t_out=rec["t_out"]),
        badge=step.get("badge") if rec["ok"] else f"Failed (exit {rec['returncode']})",
        duration=dur,
    )
    hl = step.get("highlight") or {}
    done = lead + run
    if switched:
        st.sfx.append(Sfx(t=0, kind="whoosh"))
    if a["kind"] == "terminal":
        cmd = a["command"]
        typed = len(cmd) * TYPE_S + 0.15
        st.sfx += [
            Sfx(t=lead, kind="keys", duration=typed),
            Sfx(t=lead + typed + 0.05, kind="click"),
        ]
        bw, bh = _cmd_box(cmd)
        st.highlights.append(
            Highlight(
                t_in=lead + 0.1,
                t_out=lead + typed + 0.9,
                x=4,
                y=TOP - 4,
                w=bw,
                h=bh,
                label=hl.get("command"),
            )
        )
        if hl.get("output"):
            o = hl["output"]
            st.highlights.append(
                Highlight(
                    t_in=done + 0.4,
                    t_out=dur,
                    x=4,
                    y=int(TOP - 4 + bh),
                    w=int(o["chars"] * CHAR_W + 16),
                    h=int(o["lines"] * LINE_H + 8),
                    label=o["label"],
                )
            )
        z = step.get("zoom", 1.8)
        st.camera = [
            Camera(t=0, zoom=1.15 if z > 1 else 1, cx=0, cy=0),
            Camera(t=0.7, zoom=z, cx=0, cy=0),
        ]
        if step.get("zoom_out_after"):
            st.camera += [
                Camera(t=done + 0.2, zoom=z, cx=0, cy=0),
                Camera(t=done + 1.4, zoom=1, cx=W / 2, cy=H / 2),
            ]
        else:
            st.camera.append(Camera(t=dur, zoom=z * 1.05, cx=0, cy=0))
    else:
        if a["kind"] == "open_url":
            st.sfx += [
                Sfx(t=lead + 0.05, kind="keys", duration=0.45),
                Sfx(t=lead + 0.5, kind="click"),
            ]
            st.camera = [
                Camera(t=0, zoom=2.0, cx=520, cy=100),
                Camera(t=done + 0.5, zoom=2.0, cx=520, cy=100),
                Camera(t=done + 1.6, zoom=1.0, cx=W / 2, cy=H / 2),
            ]
        else:
            st.sfx.append(Sfx(t=lead + 0.05, kind="click"))
            st.camera = [Camera(t=0, zoom=1.0, cx=W / 2, cy=H / 2)]
        if f := step.get("focus"):
            st.camera += [
                Camera(t=done + 2.2, zoom=1.0, cx=W / 2, cy=H / 2),
                Camera(t=done + 3.6, **f),
                Camera(t=dur, **{**f, "zoom": f["zoom"] * 1.04}),
            ]
        if b := hl.get("box"):
            st.highlights.append(Highlight(t_in=done + 2.6, t_out=dur, **b))
    if rec["ok"] and step.get("success"):
        st.sfx.append(Sfx(t=max(0, dur - 2.6), kind="success"))
    return st


def build(spec_dir: Path, run_dir: Path, base: str) -> Tutorial:
    spec = json.loads((spec_dir / "spec.json").read_text())
    lines = json.loads((spec_dir / "narration.json").read_text())
    audio = run_dir / "audio"
    cap = json.loads((run_dir / "capture.json").read_text())
    recs = {r["step"]: r for r in cap["records"]}
    intro = narration(audio, "intro", lines["intro"], 0.7, base)
    tut = Tutorial(
        id=base,
        title=spec["title"],
        subtitle=spec["subtitle"],
        recording_url=f"{base}/recording.mp4",
        music_url=f"{base}/audio/sfx_music.mp3",
        steps=[s["label"] for s in spec["steps"]],
    )
    tut.states.append(
        State(
            id="title",
            kind="title",
            label="",
            narration=[intro],
            duration=round(intro.at + intro.duration + 1.2, 2),
            sfx=[Sfx(t=0.25, kind="whoosh"), Sfx(t=2.7, kind="keys", duration=1.7)],
        )
    )
    prev = None
    for step in spec["steps"]:
        if step["id"] not in recs:
            break
        narr = [narration(audio, k, lines[k], 0, base) for k in step["narration"]]
        st = plan_step(step, recs[step["id"]], narr, prev)
        tut.states.append(st)
        prev = st.app
        if not st.verified:
            break
    outro = narration(audio, "outro", lines["outro"], 0.4, base)
    tut.states.append(
        State(
            id="outro",
            kind="outro",
            label="",
            narration=[outro],
            duration=round(outro.at + outro.duration + 2.2, 2),
            sfx=[
                Sfx(t=0.0, kind="whoosh"),
                Sfx(t=0.85, kind="pop"),
                Sfx(t=1.6, kind="pop"),
                Sfx(t=2.3, kind="pop"),
                Sfx(t=2.6, kind="success"),
            ],
        )
    )
    return tut


def srt(tut: Tutorial) -> str:
    def ts(x: float) -> str:
        ms = int(round(x * 1000))
        return f"{ms // 3600000:02}:{ms // 60000 % 60:02}:{ms // 1000 % 60:02},{ms % 1000:03}"

    out, n, t0 = [], 1, 0.0
    for st in tut.states:
        for nar in st.narration:
            chunk: list[Word] = []
            for i, w in enumerate(nar.words):
                chunk.append(w)
                last = i == len(nar.words) - 1
                if len(chunk) >= 7 or re.search(r"[.?!]$", w.text) or last:
                    a, b = t0 + nar.at + chunk[0].t, t0 + nar.at + chunk[-1].end
                    out.append(
                        f"{n}\n{ts(a)} --> {ts(b + 0.2)}\n{' '.join(c.text for c in chunk)}\n"
                    )
                    n, chunk = n + 1, []
        t0 += round(st.duration * tut.fps) / tut.fps
    return "\n".join(out)
