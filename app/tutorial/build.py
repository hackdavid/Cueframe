"""Turn a captured run (continuous recording + timestamped action records) and the
narration into the tutorial state graph: per-state footage windows, camera moves,
highlights, sound effects and captions. Pure functions, so edits to one state can
be re-planned and re-rendered without touching the others."""

import re
from collections.abc import Collection
from typing import Any

from app.tutorial import components
from app.tutorial.contracts import (
    Camera, Highlight, Narration, OverlaySpec, Sfx, Source, State, Tutorial, Word,
)
from app.tutorial.facts import Facts

W, H = 1920, 1080
# Terminal font metrics at CUEFRAME_SANDBOX_TERM_FONT=22 on a 1920x1080 screen.
CHAR_W, LINE_H, TOP = 18.3, 36.7, 22
PROMPT = len("user@cueframe:/work$ ")
COLS = int(W // CHAR_W)
TYPE_S = 0.035  # xdotool per-character delay
SPOKEN = {"engine-x": "nginx"}  # spelled for the voice, shown as written


def _core(text: str) -> str:
    return text.rstrip(".,!?;:").lower()


def caption_words(words: list[Word], say: dict[str, str] | None = None) -> list[Word]:
    """Show respelled terms as written: 'engine-x' -> nginx, 'J Q' -> jq, 'two hundred' -> 200."""
    spoken = {**SPOKEN, **(say or {}), "two hundred": "200"}
    phrases = sorted(((k.split(), v) for k, v in spoken.items()), key=lambda x: -len(x[0]))
    out: list[Word] = []
    i = 0
    while i < len(words):
        for toks, written in phrases:
            n = len(toks)
            if [_core(w.text) for w in words[i : i + n]] == toks:
                last = words[i + n - 1]
                tail = last.text[len(last.text.rstrip(".,!?;:")) :]
                out.append(words[i].model_copy(update={"text": written + tail, "end": last.end}))
                i += n
                break
        else:
            out.append(words[i])
            i += 1
    return out


def narration(
    voice: dict[str, Any], key: str, at: float, base: str, say: dict[str, str] | None = None
) -> Narration:
    """`voice[key]` is a synthesized line: {"text", "file", "words"}."""
    v = voice[key]
    words = [Word(**w) for w in v["words"]]
    return Narration(
        text=v["text"],
        at=at,
        audio_url=f"{base}/audio/{v['file']}",
        duration=words[-1].end if words else 0,
        words=caption_words(words, say),
    )


BOXED = ("click", "type", "double_click", "scroll", "menu", "drag")


def plan_overlay_step(step: dict[str, Any], narr: list[Narration], facts: Facts) -> State:
    """A motion-graphics state: no footage, overlays resolved against the job's facts."""
    t = 0.35
    for n in narr:
        n.at = t
        t = n.at + n.duration + 0.35
    c = components.REGISTRY.get(step["overlay"]["component"])
    want = c.default_seconds if c else 3.0
    end = max([n.at + n.duration for n in narr], default=0)
    spec = OverlaySpec(
        id=step["id"],
        component=step["overlay"]["component"],
        props=components.resolve(
            step["overlay"]["component"], step["overlay"].get("props") or {}, facts),
    )
    return State(
        id=step["id"],
        label=step["label"],
        narration=narr,
        overlays=[spec],
        badge=step.get("badge"),
        duration=round(max(end + 0.8, want + 0.8), 2),
        sfx=[Sfx(t=0.1, kind="pop")],
    )


def plan_board_step(step: dict[str, Any], board: dict[str, Any], base: str) -> State:
    """A board segment as a state: the Manim-rendered mp4 plays, narrated by the board
    plan's own voice lines (staged by the pipeline as audio/board_<step>_<i>.mp3)."""
    tl = board["tl"]
    narr = []
    for i, st in enumerate(tl["steps"]):
        words = [Word(**w) for w in st["words"]]
        narr.append(Narration(
            text=" ".join(w.text for w in words),
            at=st["start"],
            audio_url=f"{base}/audio/board_{step['id']}_{i}.mp3",
            duration=st["audio"],
            words=words,
        ))
    return State(
        id=step["id"],
        label=step["label"],
        narration=narr,
        media_url=f"{base}/board_{step['id']}.mp4",
        badge=step.get("badge"),
        duration=round(tl["seconds"] + 0.5, 2),
    )


def _cmd_box(cmd: str) -> tuple[int, int]:
    lines = -(-(PROMPT + len(cmd)) // COLS)
    width = min(W - 8, (PROMPT + len(cmd)) * CHAR_W + 16) if lines == 1 else W - 8
    return int(width), int(lines * LINE_H + 10)


FAST, CUT = (2.0, 8.0), (0.8, 16.0)  # (seconds left on screen, max speed-up)


def pace_rate(pace: str, run: float) -> float:
    """Playback speed for a step the storyboard fast-forwards or cuts: the real recording,
    sped up so a wait of `run` seconds takes about FAST/CUT seconds on screen."""
    if pace not in ("fast", "cut"):
        return 1
    show, top = FAST if pace == "fast" else CUT
    r = min(top, run / show)
    return round(r, 2) if r >= 1.5 else 1


def plan_step(
    step: dict[str, Any], rec: dict[str, Any], narr: list[Narration], prev_app: str | None
) -> State:
    a = step["action"]
    app = a.get("app") or ("terminal" if a["kind"] == "terminal" else "browser")
    rate = pace_rate(step.get("pace", "real"), rec["ended"] - rec["started"]) if rec["ok"] else 1
    run = (rec["ended"] - rec["started"]) / rate
    lead = 0.25
    switched = prev_app is not None and prev_app != app
    t = lead + (0.35 if switched else 0.1)
    for n in narr:
        n.at = t
        t = n.at + n.duration + 0.35
    if len(narr) > 1:  # the follow-up line waits for the command to finish
        narr[1].at = max(narr[1].at, lead + run + 0.3)
    end = max(n.at + n.duration for n in narr) if narr else run
    window = (rec["t_out"] - (rec["t_in"] - lead)) / rate
    # Never cut narration short: past the captured window the renderer holds the last frame.
    dur = round(max(min(window, max(lead + run + 1.6, 3.4)), end + 1.0), 2)
    st = State(
        id=step["id"],
        app=app,
        label=step["label"],
        narration=narr,
        verified=rec["ok"],
        source=Source(t_in=max(0, rec["t_in"] - lead), t_out=rec["t_out"], rate=rate),
        badge=step.get("badge") if rec["ok"] else f"Failed (exit {rec['returncode']})",
        duration=dur,
    )
    hl = step.get("highlight") or {}
    done = lead + run
    if switched:
        st.sfx.append(Sfx(t=0, kind="whoosh"))
    if a["kind"] == "terminal":
        cmd = a["command"]
        typed = (len(cmd) * TYPE_S + 0.15) / rate
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
    elif rec.get("box") and a["kind"] in BOXED:
        x, y, w, h = rec["box"]
        if a["kind"] == "drag" and rec.get("path"):  # frame the stroke, not the whole canvas
            x1, y1, x2, y2 = rec["path"]
            x, y = min(x1, x2) - 40, min(y1, y2) - 40
            w, h = abs(x2 - x1) + 80, abs(y2 - y1) + 80
        cx, cy = x + w / 2, y + h / 2
        # Big targets (calculator keys) get a gentle zoom so the rest of the app stays in view.
        z = round(max(1.2, min(1.9, W / (4 * max(w, 1)), H / (4 * max(h, 1)))), 2)
        st.camera = [
            Camera(t=0, zoom=1.0, cx=W / 2, cy=H / 2),
            Camera(t=lead + 0.1, zoom=z, cx=cx, cy=cy),
            Camera(t=done + 1.2, zoom=z, cx=cx, cy=cy),
            Camera(t=min(dur, done + 2.2), zoom=1.0, cx=W / 2, cy=H / 2),
        ]
        acts = a["kind"] in ("click", "type", "double_click", "menu", "drag")
        st.highlights.append(
            Highlight(t_in=lead, t_out=min(dur, done + 1.4), x=max(0, x - 6), y=max(0, y - 6),
                      w=w + 12, h=h + 12, label=hl.get("command"),
                      kind="callout" if acts else "box", dim=0.45 if acts else 0.0,
                      cursor=acts)  # a real action: dim the rest, show the pointer
        )  # fmt: skip
        if a["kind"] == "type":
            typed = len(a.get("text") or "") * 0.045 + 0.2
            st.sfx += [
                Sfx(t=lead + 0.3, kind="click"),
                Sfx(t=lead + 0.5, kind="keys", duration=typed),
            ]
        elif a["kind"] != "scroll":
            st.sfx.append(Sfx(t=lead + 0.3, kind="click"))
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


def teaser(steps: list[dict[str, Any]], width: int = 40) -> str:
    """Short command for the title card: the first install, else the first terminal step."""
    cmds = [s for s in steps if (s.get("action") or {}).get("kind") == "terminal"]
    pick = next((s for s in cmds if s.get("installs")), cmds[0] if cmds else None)
    if not pick:
        return ""
    cmd = re.sub(r"^(\w+=\S*\s+)+", "", pick["action"]["command"])
    cmd = re.split(r"\s+(?:&&|\|\|?|;)\s+", cmd)[0]
    return "$ " + (cmd if len(cmd) <= width else cmd[: width - 1] + "…")


def build(
    spec: dict[str, Any],
    voice: dict[str, Any],
    capture: dict[str, Any],
    base: str,
    hidden: Collection[str] = (),
) -> Tutorial:
    """Plan the whole graph from the recipe `spec` (after edits), the synthesized
    `voice` lines and the `capture` records. `hidden` steps ran but stay out of the
    video (e.g. "already installed"). A failed step ends the video honestly."""
    recs = {r["step"]: r for r in capture["records"]}
    say = spec.get("say") or {}
    shown = [s for s in spec["steps"] if s["id"] not in hidden]
    tut = Tutorial(
        id=base,
        title=spec["title"],
        subtitle=spec.get("subtitle", ""),
        recording_url=f"{base}/recording.mp4",
        music_url=f"{base}/audio/sfx_music.mp3",
        steps=[s["label"] for s in shown],
        software=spec.get("software", ""),
        kicker=spec.get("kicker", ""),
        teaser=teaser(spec["steps"]),
        done=spec.get("done") or "You did it",
        recap=spec.get("recap") or [s["label"] for s in shown][:4],
    )
    facts = Facts.model_validate(spec["facts"]) if spec.get("facts") else Facts()
    intro = narration(voice, "intro", 0.7, base, say)
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
    prev, ok = None, True
    for step in spec["steps"]:
        if step.get("scene") == "board":
            # the board plan's own voice narrates it; the spec's line is not used
            board = (capture.get("boards") or {}).get(step["id"])
            if board and step["id"] not in hidden:
                tut.states.append(plan_board_step(step, board, base))
            continue  # not rendered (older capture): skip the scene, don't break the video
        narr = [narration(voice, k, 0, base, say) for k in step["narration"]]
        if step.get("scene") == "overlay":
            if step["id"] not in hidden:
                tut.states.append(plan_overlay_step(step, narr, facts))
            continue  # scenes don't change which app the next clip switches from
        rec = recs.get(step["id"])
        if rec is None:
            break
        if step["id"] in hidden and rec["ok"]:
            continue
        st = plan_step(step, rec, narr, prev)
        tut.states.append(st)
        prev = st.app
        if not st.verified:
            ok = False
            break
    if ok:
        outro = narration(voice, "outro", 0.4, base, say)
        tut.states.append(
            State(
                id="outro",
                kind="outro",
                label="",
                narration=[outro],
                duration=round(outro.at + outro.duration + 2.2, 2),
                sfx=[
                    Sfx(t=0.0, kind="whoosh"),
                    *(Sfx(t=round(0.85 + 0.73 * i, 2), kind="pop") for i in range(len(tut.recap))),
                    Sfx(t=round(0.85 + 0.73 * len(tut.recap), 2), kind="success"),
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
