"""Production tutorial pipeline, run inside background jobs.

make: intake -> narration (ElevenLabs) -> real run in a 1080p sandbox, recorded once
and paced to the narration -> state graph -> parallel per-state render -> publish.
edit: patch states (narration, labels, zoom, highlights) -> re-voice only changed
lines -> re-render only changed segments -> publish a new version.

SQLite keeps the recipe (with edits), voice timings, capture records, the graph and
storage URLs. Recordings, audio, segments and final files live in R2 (or the local
artifacts dir); the per-tutorial work dir is only a cache and is refilled from storage.
"""

import asyncio
import dataclasses
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from app import endcard, languages
from app.agents.trending import brief_text
from app.heal import Healer, short
from app.jobs import JobContext, JobFailed
from app.sandbox.contracts import Action, live_path
from app.sandbox.manager import SandboxLimit
from app.tutorial import formats, planner, qa, recipes, shown, sources
from app.tutorial.build import build
from app.tutorial.contracts import Tutorial
from app.tutorial.render import render
from app.tutorial.voice import sound, speak

SKIP = "Already installed - leave the install out of the video"
SHOW = "Not installed - show me how to install it"
RUN = "Yes, run it and record the video"
CANCEL = "No, cancel"
DESKTOP = {"sandbox_width": 1920, "sandbox_height": 1080, "sandbox_scale": 1.5,
           "sandbox_term_font": 22, "sandbox_cpu": 4}  # fmt: skip
SFX = {
    "click": ("A single crisp, soft mouse click. Clean UI sound, dry, no reverb", 0.5),
    "keys": ("Fast, quiet laptop keyboard typing, close and dry, no music", 3.0),
    "whoosh": ("A short smooth airy swoosh transition for a tech video", 0.8),
    "success": ("A bright, pleasant two-note success chime, modern app notification", 1.2),
    "pop": ("A soft, bubbly UI pop as an element appears", 0.5),
    "music": ("Upbeat, light lo-fi electronic background loop for a tech tutorial, "
              "warm keys and soft beat, no vocals, steady", 22.0),
}  # fmt: skip
LEAD, MIN_HOLD = 1.0, 1.2  # seconds of screen before the first step / after each step
PACE = 1.0  # real-time factor for those holds (tests set 0)
MAX_TAKES = 3  # recording attempts before an honest failure video is published


def work_dir(ctx: JobContext, tid: str) -> Path:
    return ctx.settings.data_dir / "tutorials" / tid


def _audio(ctx: JobContext, tid: str) -> Path:
    path = work_dir(ctx, tid) / "public" / tid / "audio"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _keys(spec: dict[str, Any], state_id: str) -> list[str]:
    if state_id == "title":
        return ["intro"]
    if state_id == "outro":
        return ["outro"]
    return next(s["narration"] for s in spec["steps"] if s["id"] == state_id)


async def _ensure(ctx: JobContext, path: Path, stored: dict[str, Any]) -> None:
    if path.exists():
        return
    data = await ctx._manager.storage.get(stored)
    if data is None:
        raise RuntimeError(f"Missing tutorial asset {stored['key']}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


async def ensure_sfx(ctx: JobContext, audio: Path) -> None:
    """Shared sound library, generated once with ElevenLabs and kept in storage."""
    storage = ctx._manager.storage
    lib = ctx.settings.data_dir / "tutorial-assets"
    lib.mkdir(parents=True, exist_ok=True)
    for kind, (prompt, secs) in SFX.items():
        name = f"sfx_{kind}.mp3"
        cached = lib / name
        if not cached.exists():
            data = await storage.get(storage.ref(f"assets/{name}"))
            if data is None:
                ctx.thinking("Sound", f"Generating the {kind} sound")
                for attempt in range(3):
                    try:
                        data = await sound(prompt, secs, 0.6 if kind == "music" else 0.5)
                        break
                    except Exception:
                        if attempt == 2:
                            raise
                        await asyncio.sleep(2)
                await storage.put(f"assets/{name}", data, "audio/mpeg")
            cached.write_bytes(data)  # type: ignore[arg-type]
        if not (audio / name).exists():
            (audio / name).write_bytes(cached.read_bytes())


def kind(spec: dict[str, Any]) -> str:
    """What the user asked for, in their words: tutorial, reel or explainer."""
    return formats.NAMES.get(spec.get("mode") or "tutorial", "video")


def _lively(spec: dict[str, Any]) -> bool:
    return spec.get("mode") == "influencer"


async def voice_lines(
    ctx: JobContext,
    tid: str,
    lines: dict[str, str],
    voice: dict[str, Any],
    heal: Healer | None = None,
    lively: bool = False,
) -> list[str]:
    """Synthesize every line whose text changed; returns the keys that were re-voiced.
    `lively` is the more energetic delivery of influencer videos."""
    vid = os.environ.get("ELEVENLABS_VOICE_ID", "") + ("|lively" if lively else "")
    todo = [k for k, text in lines.items() if voice.get(k, {}).get("text") != text]
    sem = asyncio.Semaphore(3)
    audio = _audio(ctx, tid)
    heal = heal or Healer(ctx)

    async def one(key: str) -> None:
        text = lines[key]
        h = hashlib.sha256(f"{vid}|{text}".encode()).hexdigest()[:10]
        async with sem:
            mp3, words = await heal.retry(
                lambda: speak(text, lively=lively), f"ElevenLabs narration {key!r}"
            )
        if not words:
            raise RuntimeError(f"ElevenLabs returned no word timings for {key!r}")
        name = f"{key}-{h}.mp3"
        (audio / name).write_bytes(mp3)
        stored = await ctx._manager.storage.put(f"tutorials/{tid}/voice-{key}-{h}.mp3", mp3,
                                                "audio/mpeg")  # fmt: skip
        voice[key] = {"text": text, "file": name, "stored": stored,
                      "words": [w.model_dump() for w in words]}  # fmt: skip

    await asyncio.gather(*(one(k) for k in todo))
    return todo


def untouched(rec: Any) -> bool:
    """A GUI step that failed without acting: its target wasn't on screen, so nothing
    was clicked or typed and the screen is as the previous step left it."""
    return not rec.ok and rec.returncode == 3 and "No element matching" in (rec.error or "")


async def mend(
    ctx: JobContext, tid: str, spec: dict[str, Any], voice: dict[str, Any],
    rehearsal: tuple[Healer, dict[str, Any]], i: int, rec: Any,
) -> bool:  # fmt: skip
    """Fix step `i` in the middle of a recorded rehearsal: it failed without touching the
    screen, so the take can go on with the fixed step instead of a second take. The
    failed attempt stays out of the video. False when it can't be fixed here."""
    heal, state = rehearsal
    step = spec["steps"][i]
    tries = state.get(step["id"], 0)
    if tries >= planner.MAX_REPAIRS or state["repairs"] >= planner.MAX_TOTAL_REPAIRS:
        return False
    state["repairs"] += 1
    state[step["id"]] = tries + 1
    ctx.phase("plan", f"Fixing step {i + 1} from its real output, still recording")
    n = len(spec["steps"])
    if await planner.repair(ctx, spec, i, rec):
        return False
    brief = planner.failure(step, rec)[0].split(" Visible:")[0]
    how = planner.fixed(spec, i, n)
    heal(f"Rehearsal: step **{step['label']}** failed before acting ({brief}); rewrote it "
         f"from the real output as {how} and kept recording the take")  # fmt: skip
    await voice_lines(ctx, tid, spec["lines"], voice, heal, lively=_lively(spec))
    state["kept"] = state.get("kept", 0) + 1
    return True


async def capture(
    ctx: JobContext,
    tid: str,
    spec: dict[str, Any],
    voice: dict[str, Any],
    hidden: set[str],
    rehearsal: tuple[Healer, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Run every step for real in one sandbox while recording continuously. Each step
    holds the screen for as long as its narration needs, so clips are never frozen.
    As a `rehearsal` (healer, repair state), recording stops at a failure and the take is
    finished unrecorded, repairing in place; `rehearsal.clean` says whether the take
    shows the final plan (every step passed first time and the result needed nothing)."""
    mgr, storage = ctx._manager.sandboxes, ctx._manager.storage
    desk = dataclasses.replace(ctx.settings, **DESKTOP)  # type: ignore[arg-type]
    records: list[dict[str, Any]] = []
    steps = spec["steps"]
    ctx.phase("sandbox", "Starting a 1080p Linux desktop")
    async with mgr.session(ctx.id, ctx.conversation_id, "about:blank", settings=desk) as (sid, box):
        ctx.emit("sandbox.started", {"sandbox_id": sid, "live_url": live_path(ctx.id)})
        await planner.put_files(box, spec)
        await box.run(-1, Action(kind="terminal", command="clear", ask=False))
        t0 = await box.start_recording()
        await asyncio.sleep(LEAD * PACE)
        i = 0
        while i < len(steps):
            step = steps[i]
            ctx.phase("act", f"Step {i + 1}/{len(steps)}: {step['label']}")
            action = Action.model_validate(step["action"])
            need = 0.0
            if step["id"] not in hidden:
                ends = [voice[k]["words"][-1]["end"] for k in step["narration"]]
                need = sum(ends) + 0.6 * len(ends) + 0.3

            async def out(line: str, i: int = i) -> None:
                mgr.touch(sid)
                ctx.emit("sandbox.output", {"sandbox_id": sid, "step": i, "line": line})

            mgr.touch(sid)
            async with mgr.busy(sid):
                rec = await planner.check(box, step, await box.run(i, action, out))
            if action.kind == "terminal" and rec.ok and step.get("zoom"):
                wide = max(
                    (len(x.expandtabs()) for x in planner.output_lines(rec, action.command or "")),
                    default=0,
                )
                step["zoom"] = min(step["zoom"], planner.zoom_fit(wide))  # no clipped lines
            if step.get("output_label") and rec.ok:
                body = planner.output_lines(rec, action.command or "")
                if 0 < len(body) <= 6:
                    step.setdefault("highlight", {})["output"] = {
                        "lines": len(body), "label": step["output_label"],
                        "chars": min(100, max(len(x) for x in body)),
                    }  # fmt: skip
            spent = rec.settled - rec.started
            hold = max(MIN_HOLD, need - spent + 0.8)
            await asyncio.sleep(hold * PACE)
            mgr.touch(sid)
            rec.t_in, rec.t_out = rec.started - t0, rec.settled - t0 + hold
            rec.in_video = step["id"] not in hidden
            records.append({"step": step["id"], **rec.model_dump(mode="json")})
            ctx.emit("sandbox.action", {"sandbox_id": sid, "record": records[-1]})
            ctx.progress(0.15 + 0.45 * (i + 1) / len(steps))
            if not rec.ok:
                if rehearsal and untouched(rec):
                    async with mgr.busy(sid):  # the take keeps recording while it's fixed
                        mended = await mend(ctx, tid, spec, voice, rehearsal, i, rec)
                    if mended:
                        records[-1]["in_video"] = False
                        continue  # step i is now the fixed step (or the next one)
                break
            i += 1
        ctx.phase("upload", "Saving the recording")
        video = await box.stop_recording()
        failed = not records or not records[-1]["ok"]
        verdict = None
        if rehearsal:
            heal, state = rehearsal
            err, changed = await planner.settle(
                ctx, spec, heal, state, sid, box, len(records) - 1 if failed else None
            )
            verdict = {"error": err, "clean": not (failed or changed or err),
                       "kept_repairs": state.get("kept", 0)}  # fmt: skip
            failed = bool(err)
        await mgr.stop(sid, "job failed" if failed else "job finished")
    if verdict and not verdict["clean"]:
        return {"rehearsal": verdict, "sandbox_id": sid}
    (work_dir(ctx, tid) / "public" / tid / "recording.mp4").write_bytes(video)
    stored = await storage.put(f"tutorials/{tid}/recording.mp4", video, "video/mp4")
    sb = ctx.store.get_sandbox(sid) or {}
    return {"t0": t0, "records": records, "recording": stored, "sandbox_id": sid,
            **({"rehearsal": verdict} if verdict else {}),
            "sandbox_seconds": sb.get("seconds"), "sandbox_cost_usd": sb.get("est_cost_usd")}  # fmt: skip  # noqa: E501


async def record(
    ctx: JobContext,
    tid: str,
    spec: dict[str, Any],
    voice: dict[str, Any],
    hidden: set[str],
    heal: Healer,
) -> dict[str, Any]:
    """Record the tutorial, healing failed takes: a broken sandbox or blank capture is
    recorded again in a fresh sandbox; a step that fails for real is rewritten from its
    real output (narration re-voiced) and recorded again. After MAX_TAKES the last take
    is returned as is, so the video honestly shows the failure."""
    for take in range(1, MAX_TAKES + 1):
        last = take == MAX_TAKES
        try:
            cap = await capture(ctx, tid, spec, voice, hidden)
        except Exception as exc:
            if last or isinstance(exc, SandboxLimit):
                raise
            heal(f"The recording sandbox broke ({short(exc)}); recording again in a fresh one")
            continue
        cap["take"] = take
        recs = cap["records"]
        bad = next((i for i, r in enumerate(recs) if not r["ok"]), None)
        if bad is None:
            path = work_dir(ctx, tid) / "public" / tid / "recording.mp4"
            issues = await qa.recording(path, recs)
            if not issues:
                return cap
            if last:
                cap["qa"] = issues
                return cap
            heal(f"Quality check of take {take}: {'; '.join(issues)}. Recording again")
            continue
        step = spec["steps"][bad]
        why, _ = planner.failure(step, recs[bad])
        if last:
            return cap
        ctx.phase("plan", f"Fixing step {bad + 1} from its real output")
        n = len(spec["steps"])
        if err := await planner.repair(ctx, spec, bad, recs[bad]):
            heal(f"Step **{step['label']}** failed while recording ({why}) and {err}")
            return cap
        heal(f"Step **{step['label']}** failed while recording ({why}); rewrote it as "
             f"{planner.fixed(spec, bad, n)} and recorded again")  # fmt: skip
        await voice_lines(ctx, tid, spec["lines"], voice, heal, lively=_lively(spec))
        ctx.store.update_tutorial(tid, spec=spec, voice=voice)
    raise AssertionError("unreachable")


def _ts(x: float) -> str:
    return f"{int(x) // 60}:{int(x) % 60:02}"


def youtube_md(spec: dict[str, Any], tut: Tutorial) -> str:
    chapters, t, last = [], 0.0, -99.0
    for st in tut.states:
        name = {"title": "Intro", "outro": "Wrap-up"}.get(st.kind, st.label)
        if t - last >= 10 or not chapters:  # YouTube needs chapters of 10 s or more
            chapters.append(f"{_ts(t)} {name}")
            last = t
        t += round(st.duration * tut.fps) / tut.fps
    cmds = [s["action"]["command"] for s in spec["steps"]
            if s["action"]["kind"] == "terminal" and s["id"] in {x.id for x in tut.states}]  # fmt: skip  # noqa: E501
    software = spec.get("software", "")
    return "\n".join([
        f"# {spec['title']}{f' ({software} tutorial)' if software else ''}", "",
        "## Description", "", spec.get("summary", ""), "", *chapters, "",
        "## Commands used", "", *(f"    {c}" for c in cmds), "",
        "## Tags", "", ", ".join(spec.get("tags", [])), "",
        "Captions: upload the attached .srt. Category: Science & Technology.",
    ])  # fmt: skip


async def publish(
    ctx: JobContext, row: dict[str, Any], note: str = "", heal: Healer | None = None
) -> dict[str, Any]:
    """Build the graph from the stored inputs, render what changed, check the result
    (re-rendering once if the check fails) and post it."""
    heal = heal or Healer(ctx)
    store, storage, tid = ctx.store, ctx._manager.storage, row["id"]
    spec, voice, cap = row["spec"], row["voice"], row["capture"]
    work, audio = work_dir(ctx, tid), _audio(ctx, tid)
    ctx.phase("render", "Preparing assets")
    await _ensure(ctx, work / "public" / tid / "recording.mp4", cap["recording"])
    for v in voice.values():
        await _ensure(ctx, audio / v["file"], v["stored"])
    await ensure_sfx(ctx, audio)
    tut = build(spec, voice, cap, tid, hidden=set(spec.get("hidden", [])))
    row = store.update_tutorial(tid, status="rendering", graph=tut.model_dump(), active_job=ctx.id)

    def progress(done: int, total: int) -> None:
        label = f"Rendering steps in parallel ({done}/{total})" if total else "Nothing changed"
        ctx.progress(0.65 + 0.3 * (done / total if total else 1), label)

    async def fetch(name: str) -> bytes | None:
        return await storage.get(storage.ref(f"tutorials/{tid}/{name}"))

    async def save(name: str, path: Path) -> None:
        await storage.put(f"tutorials/{tid}/{name}", path.read_bytes(), "video/mp4")

    ctx.phase("render", "Rendering the video")
    res = await render(tut, work, fetch, save, progress, on_retry=heal)
    ctx.phase("qa", "Checking the finished video")
    issues = await qa.final(tut, res["video"], res["captions"], res["stats"]["duration"])
    if issues:
        heal(f"Quality check: {'; '.join(issues)}. Rendering every step again")
        res = await render(tut, work, fetch, save, progress,
                           force={s.id for s in tut.states}, on_retry=heal)  # fmt: skip
        issues = await qa.final(tut, res["video"], res["captions"], res["stats"]["duration"])
    issues = [*(cap.get("qa") or []), *issues]
    res["video"] = await endcard.brand(ctx, res["video"])
    n = row["version"] + 1
    up = {
        "video": await storage.put(f"tutorials/{tid}/video-v{n}.mp4",
                                   res["video"].read_bytes(), "video/mp4"),
        "captions": await storage.put(f"tutorials/{tid}/captions-v{n}.srt",
                                      res["captions"].read_bytes(), "application/x-subrip"),
        "thumbnail": await storage.put(f"tutorials/{tid}/thumbnail-v{n}.png",
                                       res["thumbnail"].read_bytes(), "image/png"),
        "youtube": await storage.put(f"tutorials/{tid}/youtube-v{n}.md",
                                     youtube_md(spec, tut).encode(), "text/markdown"),
    }  # fmt: skip
    ok = all(s.verified for s in tut.states) and not issues
    output = {**up, "stats": res["stats"], "job_id": ctx.id, "qa": issues, "healing": heal.log}
    row = store.update_tutorial(tid, status="ready" if ok else "failed", version=n, output=output)
    ctx.emit("tutorial.ready", {"tutorial_id": tid, "version": n, "video_url": up["video"]["url"]})
    st = res["stats"]
    rows = "\n".join(
        f"| {i} | {s.label or s.kind.title()} | {'yes' if s.verified else '**failed**'} |"
        f" {s.duration:.1f}s |"
        for i, s in enumerate(tut.states, 1)
    )
    failed = next((s for s in tut.states if not s.verified), None)
    head = (
        f"**Step failed: {failed.label}.** The video shows exactly what happened, "
        "up to the failure."
        if failed
        else f"**The video failed its quality check:** {'; '.join(issues)}."
        if issues
        else f"**{'Updated' if n > 1 else 'Your tutorial is ready'}: {spec['title']}** "
        f"(version {n}, {st['duration']:.0f} s, 1080p)"
    )
    cost = ""
    if cap.get("sandbox_seconds") is not None and n == 1:
        cost = f" The sandbox ran {cap['sandbox_seconds']:.0f} s (about ${cap['sandbox_cost_usd']:.4f})."  # noqa: E501
    healed = heal.summary()
    ctx.reply(
        f"{head}\n\n{note + chr(10) + chr(10) if note else ''}"
        f"{healed + chr(10) + chr(10) if healed else ''}"
        f'<video src="{up["video"]["url"]}" poster="{up["thumbnail"]["url"]}" controls></video>\n\n'
        f"[Captions (.srt)]({up['captions']['url']}) · [Thumbnail]({up['thumbnail']['url']}) · "
        f"[YouTube title, description and chapters]({up['youtube']['url']}) · "
        f"[Get a share link](/share/{tid})\n\n"
        f"Rendered {st['rendered']} of {st['states']} steps in {st['render_seconds']:.0f} s.{cost}\n\n"  # noqa: E501
        "| # | Step | Verified | Length |\n|---|---|---|---|\n" + rows + "\n\n"
        'To change something, just say it, e.g. *"make the intro shorter"* or '
        '*"zoom in more on the curl output"*. Only the steps you change are re-rendered.'
    )
    result = {"tutorial_id": tid, "version": n, **output}
    if failed:
        raise JobFailed(f"Step {failed.label!r} failed", result)
    if issues:
        raise JobFailed(f"Quality check failed: {'; '.join(issues)}", result)
    return result


async def make(
    ctx: JobContext,
    recipe_id: str | None = None,
    goal: str | None = None,
    mode: planner.Mode = "tutorial",
    trending: bool = False,
    seconds: int | None = None,
) -> dict[str, Any]:
    """Make a tutorial from a hand-written recipe, or plan one for `goal`. `mode` is
    the style (tutorial, influencer reel or explainer), `seconds` the target length (the
    plan is then storyboarded first); `trending` first finds today's item."""
    missing = [k for k in ("ELEVENLABS_API_KEY", "ELEVENLABS_VOICE_ID") if not os.environ.get(k)]
    if missing:
        raise RuntimeError(f"Tutorial narration needs {', '.join(missing)} on the server")
    conv = ctx.store.get_conversation(ctx.conversation_id) if ctx.conversation_id else None
    if not conv:
        raise ValueError("tutorial.make needs a conversation")
    if recipe_id:
        spec = recipes.get(recipe_id)
    else:
        files = planner.attached(ctx)
        goal = planner.with_files(goal or ctx.input.get("text", ""), files)
        brief, kicker = "", ""
        if not trending and (brief := await sources.brief(ctx, goal, files)):
            kicker = "FROM YOUR SOURCE"
            ctx.reply("I'll build the video from your source: every fact in it comes from "
                      "what you attached or linked.")  # fmt: skip
        if trending:
            found = await planner.scout(ctx, goal)
            if isinstance(found, str):
                ctx.reply(f"I couldn't find a trending item to make a video about: {found}")
                return {"routed": "make_tutorial", "planned": False, "reason": found}
            brief = brief_text(found)
            kicker = f"TRENDING · {found.source.upper()}"[:44]
            ctx.reply(f"**Trending pick: {found.name}** ({found.source}, {found.why_trending})"
                      f"\n\n{found.one_liner}\n\n{found.url}")  # fmt: skip
        language = (ctx.input.get("job_input") or {}).get("language") or languages.detect(
            ctx.input.get("text")
        )
        planned = await planner.plan(ctx, goal, mode=mode, brief=brief, seconds=seconds,
                                     language=language)  # fmt: skip
        if isinstance(planned, str):
            ctx.reply(f"I can't make that tutorial: {planned}")
            return {"routed": "make_tutorial", "planned": False, "reason": planned}
        spec, recipe_id = planned, "planned"
        spec["files"] = files
        spec["brief"] = brief
        if brief:
            spec["kicker"] = kicker
        ctx.reply(
            f"**{spec['title']}**: here's the plan. I'll record a rehearsal of it; if every "
            "step works first time that's the video, otherwise I fix the steps and record "
            f"again.\n\n{planner.describe_plan(spec)}"
        )
    heal = Healer(ctx)
    answers: dict[str, bool] = {}  # install command -> already installed (left out)
    ctx.phase("intake", "Checking with you before running terminal commands")
    asked = await intake(ctx, spec, answers)
    if recipe_id == "planned" and not asked:
        ans = await ctx.ask(
            f"This {kind(spec)} runs the steps above in a private Linux sandbox. Shall I go ahead?",
            [RUN, CANCEL],
            key="confirm",
        )
        if ans == CANCEL:
            ctx.reply("Okay, I won't run it. Nothing was started.")
            return {"routed": "make_tutorial", "planned": True, "cancelled": True}
    spec["hidden"] = _hidden(spec, answers)
    spec["healing"] = heal.log
    row = ctx.store.create_tutorial(conv["user_id"], conv["id"], ctx.id, recipe_id,
                                    spec["title"], spec)  # fmt: skip
    tid = row["id"]
    row = ctx.store.update_tutorial(tid, active_job=ctx.id)
    ctx.emit("tutorial.created", {"tutorial_id": tid})
    if recipe_id == "planned":
        row = await first_take(ctx, row, answers, heal)
    out = await produce(ctx, row, heal)
    sb = (row.get("spec") or spec).get("storyboard") or {}
    if sb.get("caption") and isinstance(out, dict):
        ctx.reply(formats.social(sb))
        out["social"] = {"caption": sb["caption"], "hashtags": sb.get("hashtags") or []}
    return out


def _hidden(spec: dict[str, Any], answers: dict[str, bool]) -> list[str]:
    return sorted(s["id"] for s in spec["steps"] if answers.get(s["action"].get("command", "")))


async def first_take(
    ctx: JobContext, row: dict[str, Any], answers: dict[str, bool], heal: Healer
) -> dict[str, Any]:
    """Rehearse a planned tutorial while recording it. A take where every step passed
    first time and the result showed what was asked for is the video; any fix means
    the fixed plan is recorded again in a fresh sandbox, so the video only shows what
    really worked. A plan that can't be fixed is planned again once around the failure."""
    tid, spec = row["id"], row["spec"]
    heal.save = lambda: ctx.store.update_tutorial(tid, spec=spec)
    voice: dict[str, Any] = {}
    ctx.phase("voice", "Recording the narration")
    await voice_lines(ctx, tid, spec["lines"], voice, heal, lively=_lively(spec))
    ctx.store.update_tutorial(tid, status="capturing", voice=voice)
    ctx.phase("rehearse", "Rehearsing every step, recorded; the take is kept if all pass")
    try:
        cap = await capture(ctx, tid, spec, voice, set(spec["hidden"]),
                            rehearsal=(heal, {"repairs": 0}))  # fmt: skip
    except SandboxLimit:
        raise
    except Exception as exc:
        heal(f"The rehearsal sandbox broke ({short(exc)}); rehearsing again in a fresh one")
        cap = None
    try:
        if cap is None:
            spec = await rehearsed(ctx, spec, answers, heal)
        elif why := cap["rehearsal"]["error"]:
            spec = await replanned(ctx, spec, why, answers, heal)
    except JobFailed:
        ctx.store.delete_tutorial(tid)  # nothing worked, so there's no tutorial to keep
        raise
    if cap and cap["rehearsal"]["clean"]:
        path = work_dir(ctx, tid) / "public" / tid / "recording.mp4"
        if not (issues := await qa.recording(path, cap["records"])):
            cap["take"] = 1
            return ctx.store.update_tutorial(tid, spec=spec, voice=voice, capture=cap,
                                             assets={"recording": cap["recording"]})  # fmt: skip
        heal(f"Quality check of the rehearsal take: {'; '.join(issues)}. Recording again")
    spec["hidden"] = _hidden(spec, answers)
    return ctx.store.update_tutorial(tid, title=spec["title"], spec=spec, voice=voice)


async def intake(ctx: JobContext, spec: dict[str, Any], answers: dict[str, bool]) -> bool:
    """Ask about every install step not asked before; True if anything was asked."""
    asked = False
    for step in spec["steps"]:
        a = step["action"]
        if a["kind"] == "terminal" and a.get("ask", True) and a["command"] not in answers:
            ans = await ctx.ask(
                f"This {kind(spec)} runs `{a['command']}` in a terminal. "
                f"Is {spec.get('software') or 'it'} already installed on your machine?",
                [SKIP, SHOW],
                key=f"install_{step['id']}" + (f"_{len(answers)}" if answers else ""),
            )
            answers[a["command"]] = ans == SKIP
            asked = True
    return asked


async def rehearsed(
    ctx: JobContext, spec: dict[str, Any], answers: dict[str, bool], heal: Healer
) -> dict[str, Any]:
    """Rehearse the plan; if a step can't be repaired, plan again once around that
    failure (asking about any new install) and rehearse the new plan."""
    why = await planner.rehearse(ctx, spec, heal)
    return await replanned(ctx, spec, why, answers, heal) if why else spec


async def replanned(
    ctx: JobContext, spec: dict[str, Any], why: str, answers: dict[str, bool], heal: Healer
) -> dict[str, Any]:
    """Plan again once around a rehearsal failure (asking about any new install) and
    rehearse the new plan."""
    heal(f"The first plan couldn't work here ({why}); planned the tutorial again around it")
    again = await planner.plan(ctx, spec["goal"], avoid=why, mode=spec.get("mode", "tutorial"),
                               brief=spec.get("brief", ""), seconds=spec.get("seconds"),
                               board=spec.get("storyboard"))  # fmt: skip
    if isinstance(again, str):
        raise JobFailed(f"The rehearsal failed, so nothing was recorded: {why}. {again}",
                        {"rehearsal": why})  # fmt: skip
    again["files"] = spec.get("files") or []
    again["brief"] = spec.get("brief", "")
    again["kicker"] = spec.get("kicker") or again.get("kicker", "")
    ctx.reply(
        f"The first plan didn't work in the sandbox ({why}), so I planned it again. "
        f"**{again['title']}**:\n\n{planner.describe_plan(again)}"
    )
    await intake(ctx, again, answers)
    if again_why := await planner.rehearse(ctx, again, heal):
        raise JobFailed(f"The rehearsal failed twice, so nothing was recorded: {again_why}",
                        {"rehearsal": again_why})  # fmt: skip
    return again


async def produce(ctx: JobContext, row: dict[str, Any], heal: Healer) -> dict[str, Any]:
    """Narrate, record (healing failed takes) and publish a stored tutorial. Each stage
    is saved, so a resumed job skips what is already done."""
    tid, spec = row["id"], row["spec"]
    spec["healing"] = heal.log
    heal.save = lambda: ctx.store.update_tutorial(tid, spec=spec)
    heal.save()
    voice = row.get("voice") or {}
    hidden = set(spec.get("hidden", []))
    if not row.get("capture"):
        ctx.phase("voice", "Recording the narration")
        ctx.progress(0.05)
        await voice_lines(ctx, tid, spec["lines"], voice, heal, lively=_lively(spec))
        ctx.store.update_tutorial(tid, status="capturing", voice=voice)
        cap = await record(ctx, tid, spec, voice, hidden, heal)
        row = ctx.store.update_tutorial(
            tid, spec=spec, voice=voice, capture=cap, assets={"recording": cap["recording"]}
        )
    cap = row["capture"]
    if not cap.get("narration_checked"):
        ctx.phase("qa", "Checking each narration line against the recording")
        path = work_dir(ctx, tid) / "public" / tid / "recording.mp4"
        await _ensure(ctx, path, cap["recording"])
        if await shown.check(ctx, spec, cap["records"], path, heal):
            await voice_lines(ctx, tid, spec["lines"], voice, heal, lively=_lively(spec))
        cap["narration_checked"] = True
        row = ctx.store.update_tutorial(tid, spec=spec, voice=voice, capture=cap)
    return await publish(ctx, row, heal=heal)


async def resume(ctx: JobContext, tid: str) -> dict[str, Any]:
    """Continue a tutorial whose job was cut off by a server restart."""
    row = ctx.store.get_tutorial(tid)
    if row is None:
        raise ValueError(f"Unknown tutorial {tid}")
    heal = Healer(ctx, row["spec"].setdefault("healing", []))
    stage = "rendering" if row.get("capture") else "recording"
    heal(f"The server restarted during this tutorial; resumed it at the {stage} stage")
    ctx.reply(f"The server restarted while I was making **{row['title']}**. "
              f"Picking it up again at the {stage} stage.")  # fmt: skip
    row = ctx.store.update_tutorial(tid, active_job=ctx.id, spec=row["spec"])
    return await produce(ctx, row, heal)


class StateEdit(BaseModel):
    state: str = Field(description="State id to change: 'title', 'outro' or a step id")
    narration: list[str] | None = Field(
        None, description="New spoken lines for this state, same count as before"
    )
    label: str | None = Field(None, description="New step label (or video title for 'title')")
    zoom: float | None = Field(None, ge=1, le=3, description="Camera zoom on the key area")
    highlight: str | None = Field(None, description="New text for the highlight label")
    cut: bool | None = Field(None, description="True: leave this step out of the video")
    pace: Literal["real", "fast", "cut"] | None = Field(None, description="How the step plays")


class EditOp(BaseModel):
    state: str = Field(description="State id: 'title', 'outro' or a step id")
    field: Literal["narration", "label", "zoom", "highlight", "cut", "pace"]
    value: str = Field(
        description="The new value. narration: the full new spoken lines, one per line; "
        "label: the new text; zoom: a number from 1 to 3; highlight: the new label text; "
        "cut: 'yes' to leave the step out of the video, 'no' to bring it back; pace: "
        "'real' (real time), 'fast' (fast-forward) or 'cut' (jump to the result)"
    )

    def to_edit(self) -> StateEdit:
        if self.field == "narration":
            lines = [x.strip() for x in self.value.splitlines() if x.strip()]
            return StateEdit(state=self.state, narration=lines)
        if self.field == "cut":
            return StateEdit(state=self.state, cut=self.value.strip().lower() != "no")
        if self.field == "pace":
            pace = self.value.strip().lower()
            return StateEdit(state=self.state, pace=pace if pace in PACES else "fast")
        if self.field == "zoom":
            return StateEdit(state=self.state, zoom=min(3.0, max(1.0, float(self.value))))
        return StateEdit(state=self.state, **{self.field: self.value.strip()})


class EditPlan(BaseModel):
    edits: list[EditOp]
    summary: str = Field(description="One sentence telling the user what changed")
    reply: str = Field(description="Empty, unless the request cannot be done by editing "
                       "states: then explain why and leave edits empty")  # fmt: skip


PACES = ("real", "fast", "cut")

EDIT_RULES = """You edit a finished video (a tutorial, reel or explainer) that is stored as
a graph of states cut from one real screen recording. Turn the user's request into the
smallest set of state edits. Only change what they asked for. What you can do:
- the hook / opening line is the 'title' state's narration; the closing line is 'outro'.
- tone ("more energetic", "calmer", "funnier"): rewrite the narration of every state in
  that tone, same facts, same number of lines per state.
- length ("make it 30 s"): each state's length is listed. Reach the target by, in order:
  shorter narration lines, `pace` 'fast' or 'cut' for slow steps (installs, typing,
  waits, page loads), then `cut` whole steps that aren't the main point. Never cut the
  step that shows the main result, and never cut every step. Narration plays at about
  2.5 words a second.
- cut a part ("cut the install part"): `cut` 'yes' on those steps; 'no' brings one back.
  Then fix narration of the next shown step if it refers to the cut one.
Narration is read by a text-to-speech voice: write tool names the way they are said
(e.g. 'engine-x' for nginx, 'package manager' for apt) and keep each line short and
natural. Never claim anything the recorded run did not show. You cannot re-record
actions, add footage or change what happened on screen; if asked to, set `reply` and
explain that it needs a new video.
Every edit must carry the new value itself: the full rewritten narration lines, the new
label, the new zoom number, or the new highlight text. Example for "shorten the intro":
{"edits": [{"state": "title", "field": "narration", "value": "<the new, shorter intro>"}],
 "summary": "Shortened the intro narration.", "reply": ""}"""


def describe(row: dict[str, Any]) -> str:
    spec, lines = row["spec"], row["spec"]["lines"]
    states = (row.get("graph") or {}).get("states", [])
    total = sum(st.get("duration") or 0 for st in states)
    out = [f"Format: {kind(spec)}", f"Title: {spec['title']}", f"Length now: {total:.0f} s",
           "States:"]  # fmt: skip
    hidden, shown = set(spec.get("hidden") or []), {st["id"] for st in states}
    for st in states:
        keys = _keys(spec, st["id"])
        step = next((s for s in spec["steps"] if s["id"] == st["id"]), {})
        out.append(
            f"- {st['id']} ({st['kind']}, {st.get('app') or '-'}): label={st['label']!r} "
            f"{st.get('duration', 0):.1f} s, pace={step.get('pace', 'real')} "
            f"zoom={step.get('zoom', (step.get('focus') or {}).get('zoom', 1.8))} "
            f"narration={[lines[k] for k in keys]}"
        )
    cut = [s for s in spec["steps"] if s["id"] in hidden and s["id"] not in shown]
    for s in cut:
        out.append(f"- {s['id']} (cut, not in the video): label={s['label']!r}")
    return "\n".join(out)


def _recorded(row: dict[str, Any]) -> set[str]:
    """Steps the recording really shows working, so they can be brought back."""
    return {r["step"] for r in (row.get("capture") or {}).get("records", []) if r.get("ok")}


def apply(row: dict[str, Any], edits: list[StateEdit]) -> list[str]:
    spec, changed = row["spec"], []
    states = {s["id"] for s in (row.get("graph") or {}).get("states", [])}
    states |= {s["id"] for s in spec["steps"] if s["id"] in (spec.get("hidden") or [])}
    for e in edits:
        if e.state not in states:
            raise ValueError(f"Unknown state {e.state!r}; states are {sorted(states)}")
        step = next((s for s in spec["steps"] if s["id"] == e.state), None)
        if e.narration:
            for k, text in zip(_keys(spec, e.state), e.narration, strict=False):
                spec["lines"][k] = text.strip()
        if e.label:
            if e.state == "title":
                spec["title"] = e.label
            elif step:
                step["label"] = e.label
        if e.zoom and step:
            if step["action"]["kind"] == "terminal":
                step["zoom"] = e.zoom
            elif step.get("focus"):
                step["focus"]["zoom"] = e.zoom
        if e.cut is not None and step:
            hidden = spec.setdefault("hidden", [])
            if e.cut and e.state not in hidden:
                if len([s for s in spec["steps"] if s["id"] not in hidden]) <= 1:
                    raise ValueError("Cutting that would leave no steps in the video")
                hidden.append(e.state)
            elif not e.cut and e.state in hidden and e.state in _recorded(row):
                hidden.remove(e.state)
        if e.pace and step:
            step["pace"] = e.pace
        if e.highlight and step:
            hl = step.setdefault("highlight", {})
            if "box" in hl:
                hl["box"]["label"] = e.highlight
            else:
                hl["command"] = e.highlight
        changed.append(e.state)
    return changed


async def edit(
    ctx: JobContext,
    tid: str,
    instruction: str | None = None,
    edits: list[StateEdit] | None = None,
) -> dict[str, Any]:
    row = ctx.store.get_tutorial(tid)
    if row is None or not row.get("capture"):
        raise ValueError("That tutorial has no finished recording to edit")
    note = ""
    if edits is None:
        if ctx.llm is None or not ctx.llm.configured:
            raise RuntimeError("Editing from chat needs MODEL_API_KEY")
        ctx.phase("plan", "Working out which steps to change")
        plan, _ = await ctx.llm.parse(
            EditPlan,
            purpose="tutorial.edit",
            model=ctx.settings.research_model,
            instructions=EDIT_RULES,
            input=f"{describe(row)}\n\nUser request: {instruction}",
        )
        ctx.thinking("Plan", json.dumps(plan.model_dump()))
        if plan.reply and not plan.edits:
            ctx.reply(plan.reply)
            return {"tutorial_id": tid, "edits": []}
        edits, note = [op.to_edit() for op in plan.edits], plan.summary
    before = json.dumps(row["spec"], sort_keys=True)
    states = apply(row, edits)
    ctx.phase("voice", "Re-recording changed narration")
    revoiced = await voice_lines(
        ctx, tid, row["spec"]["lines"], row["voice"], lively=_lively(row["spec"])
    )
    if edits and json.dumps(row["spec"], sort_keys=True) == before:
        ctx.reply("That request didn't change anything in the tutorial, so I kept the current "
                  "version. Try naming the step and what to change, e.g. *\"shorten the intro "
                  "narration\"*.")  # fmt: skip
        return {"tutorial_id": tid, "edits": [e.model_dump() for e in edits], "changed": []}
    row = ctx.store.update_tutorial(tid, spec=row["spec"], voice=row["voice"])
    done = [f"edited {', '.join(dict.fromkeys(states))}"] if states else []
    if revoiced:
        done.append(f"new narration for {', '.join(revoiced)}")
    if done:
        note = f"{note} ({'; '.join(done)})".strip()
    return await publish(ctx, row, note)
