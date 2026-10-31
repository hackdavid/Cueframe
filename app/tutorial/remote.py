"""Server side of the Modal render runner: stage a manifest, run the render in a
headless sandbox (see app.tutorial.remote_render for the in-sandbox half) and pull
the finished files back. Assets and the segment cache go straight between the
sandbox and R2, so nothing big flows through the server."""

import asyncio
import dataclasses
import json
import time
from collections.abc import Callable, Collection
from pathlib import Path
from typing import Any

from app.jobs import JobContext
from app.log import fields, get_logger
from app.tutorial.contracts import Tutorial
from app.tutorial.pipeline import SFX, _audio, ensure_sfx

log = get_logger("render.modal")
POLL_SECONDS = 5


async def _poll(box: Any, tid: str,
                progress: Callable[[int, int, str | None], None]) -> dict[str, Any]:
    """Watch the driver: forward segment progress, return the result, raise on crash."""
    while True:
        await asyncio.sleep(POLL_SECONDS)
        res = await box.exec(
            "echo __P__; cat /work/progress.json 2>/dev/null; echo; echo __R__; "
            "cat /work/result.json 2>/dev/null; echo; echo __F__; "
            "cat /work/failed.txt 2>/dev/null; echo; echo __S__; "
            "pgrep -f remote_render >/dev/null && echo RUNNING || echo GONE",
            timeout=30,
        )
        parts = res.stdout.split("__")
        secs = {parts[i]: parts[i + 1].strip() for i in range(1, len(parts) - 1, 2)}
        if secs.get("F"):
            raise RuntimeError(f"remote render crashed: {secs['F'][-800:]}")
        if secs.get("R"):
            return json.loads(secs["R"])
        if secs.get("S") != "RUNNING":
            tail = await box.exec("tail -c 2000 /work/render.err 2>/dev/null", timeout=30)
            raise RuntimeError(f"remote render died without a result: {tail.stdout[-800:]}")
        try:
            p = json.loads(secs.get("P") or "{}")
            progress(p.get("done", 0), p.get("total", 0), p.get("state"))
        except json.JSONDecodeError:
            pass  # a partial write; the next poll gets it


def asset_list(storage: Any, voice: dict[str, Any], cap: dict[str, Any],
               tut: Tutorial) -> list[tuple[str, str]]:  # fmt: skip
    """(R2 key, path under public/<tid>) for everything the graph references: the
    recording, every narration line, SFX, board-scene clips and their narration, and
    logo-wall avatars. Derived from the graph so a new asset type can't be forgotten."""
    assets = [(cap["recording"]["key"], "recording.mp4")]
    assets += [(v["stored"]["key"], f"audio/{v['file']}") for v in voice.values()]
    assets += [(storage.ref(f"assets/sfx_{k}.mp3")["key"], f"audio/sfx_{k}.mp3") for k in SFX]
    for sid, b in (cap.get("boards") or {}).items():  # board scenes: clip + narration
        if not b:
            continue
        assets.append((b["video"]["key"], f"board_{sid}.mp4"))
        assets += [(r["key"], f"audio/board_{sid}_{i}.mp3") for i, r in enumerate(b["audio"])]
    assets += [(r["key"], f"logos/{org}.png")
               for org, r in (cap.get("logos") or {}).items()]  # fmt: skip
    referenced = {Path(n.audio_url).name for st in tut.states for n in st.narration
                  if n.audio_url} | {f"board_{s.id}.mp4" for s in tut.states if s.media_url}
    missing = {r for r in referenced if not any(rel.endswith(r) for _, rel in assets)}
    if missing:
        raise RuntimeError(f"render assets missing from the manifest: {sorted(missing)}")
    return assets


async def render_modal(
    ctx: JobContext,
    row: dict[str, Any],
    tut: Tutorial,
    work: Path,
    progress: Callable[[int, int, str | None], None],
    force: Collection[str] = (),
) -> dict[str, Any]:
    """`render()` in a headless Modal sandbox. Returns the same dict, plus
    `qa_issues` checked in the sandbox (the server never runs ffmpeg here)."""
    t0 = time.monotonic()
    settings, storage, tid = ctx.settings, ctx._manager.storage, tut.id
    if storage.backend != "r2":
        raise RuntimeError("remote render needs R2 storage (set CUEFRAME_RENDER_RUNNER=local "
                           "or configure R2_*)")  # noqa: E501
    await ensure_sfx(ctx, _audio(ctx, tid))  # SFX live in R2; this fills any gaps
    voice, cap = row["voice"], row["capture"]
    assets = asset_list(storage, voice, cap, tut)
    manifest = {
        "tid": tid,
        "tutorial": tut.model_dump(),
        "force": sorted(force),
        "assets": assets,
    }
    log.info("render staged", extra=fields(tutorial=tid, assets=len(assets),
                                           force=len(force), states=len(tut.states)))
    cfg = dataclasses.replace(
        settings, sandbox_image="render", sandbox_cpu=settings.render_cpu,
        sandbox_memory_mb=settings.render_memory_mb,
        sandbox_max_seconds=settings.render_max_seconds, sandbox_idle_seconds=300,
    )
    mgr = ctx._manager.sandboxes
    async with (
        mgr.session(ctx.id, ctx.conversation_id, settings=cfg) as (sid, box),
        mgr.busy(sid),
    ):
        await box.write_file("/work/manifest.json", json.dumps(manifest).encode())
        start = await box.exec(
            "rm -f /work/result.json /work/failed.txt /work/progress.json; "
            "cd /opt/cueframe && "
            "nohup python -m app.tutorial.remote_render /work "
            ">/work/render.out 2>/work/render.err & echo started",
            timeout=30,
        )
        if start.returncode != 0:
            raise RuntimeError(f"could not start remote render: {start.stderr[-300:]}")
        result = await _poll(box, tid, progress)
    out_dir = work / "out"
    out_dir.mkdir(parents=True, exist_ok=True)
    names = {"video": "tutorial.mp4", "captions": "captions.srt", "thumbnail": "thumbnail.png"}
    res: dict[str, Any] = {"stats": result["stats"], "qa_issues": result["qa_issues"]}
    for field, name in names.items():
        data = await storage.get({"backend": "r2", "key": result["out"][field]})
        if data is None:
            raise RuntimeError(f"remote render output missing: {result['out'][field]}")
        (out_dir / name).parent.mkdir(parents=True, exist_ok=True)
        (out_dir / name).write_bytes(data)
        res[field] = out_dir / name
    log.info("render done", extra=fields(tutorial=tid, runner="modal",
                                         seconds=round(time.monotonic() - t0, 1),
                                         qa_issues=len(result["qa_issues"])))
    return res
