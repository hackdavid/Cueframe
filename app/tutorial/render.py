"""Render a tutorial graph to a YouTube-ready MP4.

Each state is rendered as its own short, muted segment by Remotion, in parallel and
cached by a hash of the state, so an edit re-renders only the states it touched.
Segments are joined without re-encoding; the soundtrack (narration, SFX, music
ducked under the voice) is mixed separately and loudness-normalised to -14 LUFS.

Work dir layout: `public/<tutorial id>/{recording.mp4,audio/*.mp3}` (what Remotion
serves), `segments/<hash>.mp4` (cache), `out/` (final files).
"""

import asyncio
import hashlib
import json
import shutil
import time
from collections.abc import Awaitable, Callable, Collection
from pathlib import Path
from typing import Any

from app.heal import anything, retry
from app.log import fields, get_logger
from app.tutorial import qa
from app.tutorial.build import srt
from app.tutorial.contracts import Tutorial

log = get_logger("render")
RENDERER = Path(__file__).resolve().parents[2] / "renderer"
PARALLEL = 4
AF = "aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo"
SFX_VOL = {"click": 0.45, "keys": 0.22, "whoosh": 0.35, "success": 0.4, "pop": 0.35}

Fetch = Callable[[str], Awaitable[bytes | None]]
Save = Callable[[str, Path], Awaitable[None]]
Progress = Callable[[int, int], None]


async def _run(*cmd: str, cwd: Path | None = None) -> None:
    proc = await asyncio.create_subprocess_exec(
        *cmd, cwd=cwd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
    )
    out, _ = await proc.communicate()
    if proc.returncode:
        raise RuntimeError(f"{cmd[0]} {cmd[1]} failed: {out.decode(errors='replace')[-800:]}")


def code_hash() -> str:
    files = sorted(p for p in (RENDERER / "src").rglob("*") if p.is_file())
    return hashlib.sha256(b"".join(p.read_bytes() for p in files)).hexdigest()[:8]


def segment_hashes(tut: Tutorial) -> list[str]:
    code = code_hash()
    steps = json.dumps([tut.steps, tut.software, tut.teaser, tut.done, tut.recap])
    return [
        hashlib.sha256((code + st.model_dump_json() + steps).encode()).hexdigest()[:12]
        for st in tut.states
    ]


async def _segment(bundle: Path, props: Path, a: int, b: int, out: Path, fps: int = 30) -> None:
    tmp = out.with_suffix(".part.mp4")
    await _run(
        "npx", "remotion", "render", str(bundle), "Tutorial", str(tmp), f"--props={props}",
        f"--frames={a}-{b}", "--muted", "--concurrency=2", "--codec=h264", "--crf=16",
        "--pixel-format=yuv420p", "--log=error",
        cwd=RENDERER,
    )  # fmt: skip
    got, want = qa.duration(await qa.probe(tmp)), (b - a + 1) / fps
    if abs(got - want) > 2.5 / fps:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"segment is {got:.2f} s, expected {want:.2f} s")
    tmp.rename(out)


async def _mix_audio(tut: Tutorial, audio: Path, total: float, out: Path) -> None:
    voice, fx, inputs = [], [], []
    t0 = 0.0
    for st in tut.states:
        for n in st.narration:
            inputs += ["-i", str(audio / Path(n.audio_url or "").name)]
            voice.append((inputs.count("-i") - 1, t0 + n.at))
        for s in st.sfx:
            inputs += ["-stream_loop", "-1", "-i", str(audio / f"sfx_{s.kind}.mp3")]
            fx.append((inputs.count("-i") - 1, t0 + s.t, SFX_VOL[s.kind], s.duration or 1.6))
        t0 += round(st.duration * tut.fps) / tut.fps
    inputs += ["-stream_loop", "-1", "-i", str(audio / "sfx_music.mp3")]
    music = inputs.count("-i") - 1
    parts, vl, fl = [], [], []
    for i, (idx, at) in enumerate(voice):
        ms = int(at * 1000)
        parts.append(f"[{idx}:a]{AF},adelay={ms}|{ms}[v{i}]")
        vl.append(f"[v{i}]")
    for i, (idx, at, v, d) in enumerate(fx):
        ms = int(at * 1000)
        parts.append(
            f"[{idx}:a]atrim=0:{d:.2f},afade=t=out:st={max(0, d - 0.12):.2f}:d=0.12,{AF},"
            f"adelay={ms}|{ms},volume={v}[f{i}]"
        )
        fl.append(f"[f{i}]")
    parts.append(f"{''.join(vl)}amix=inputs={len(vl)}:normalize=0,asplit=2[voice][key]")
    parts.append(f"{''.join(fl)}amix=inputs={len(fl)}:normalize=0[fx]")
    parts.append(
        f"[{music}:a]atrim=0:{total:.2f},{AF},volume=0.28,afade=t=in:d=1.5,"
        f"afade=t=out:st={max(0, total - 3):.2f}:d=3[mus]"
    )
    parts.append("[mus][key]sidechaincompress=threshold=0.02:ratio=8:attack=20:release=400[duck]")
    parts.append(
        f"[voice][fx][duck]amix=inputs=3:normalize=0,apad,atrim=0:{total:.3f},"
        "loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000[out]"
    )
    await _run("ffmpeg", "-y", "-loglevel", "error", *inputs, "-filter_complex", ";".join(parts),
               "-map", "[out]", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", str(out))  # fmt: skip  # noqa: E501


async def render(
    tut: Tutorial,
    work: Path,
    fetch: Fetch,
    save: Save,
    progress: Progress,
    force: Collection[str] = (),
    on_retry: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Render `tut` (assets already staged under `work/public`). `fetch`/`save` sync
    cached segments with object storage so another server can reuse them. States in
    `force` are rendered again even when cached (used after a failed quality check)."""
    t_start = time.monotonic()
    seg_dir, out = work / "segments", work / "out"
    seg_dir.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)
    props = out / "props.json"
    props.write_text(json.dumps({"tutorial": tut.model_dump()}))
    bundle = work / "bundle"
    shutil.rmtree(bundle, ignore_errors=True)
    await _run("npx", "remotion", "bundle", "src/index.ts", f"--out-dir={bundle}",
               f"--public-dir={work / 'public'}", "--log=error", cwd=RENDERER)  # fmt: skip
    hashes = segment_hashes(tut)
    segs, todo, a = [], [], 0
    for st, h in zip(tut.states, hashes, strict=True):
        n = round(st.duration * tut.fps)
        seg = seg_dir / f"{h}.mp4"
        if st.id in force:
            seg.unlink(missing_ok=True)
        elif not seg.exists() and (data := await fetch(f"seg-{h}.mp4")):
            seg.write_bytes(data)
        if not seg.exists():
            todo.append((st.id, a, a + n - 1, seg))
        segs.append(seg)
        a += n
    total = a / tut.fps
    done, times = 0, {}
    progress(0, len(todo))
    sem = asyncio.Semaphore(PARALLEL)

    async def one(sid: str, f0: int, f1: int, seg: Path) -> None:
        nonlocal done
        async with sem:
            t = time.monotonic()
            await retry(lambda: _segment(bundle, props, f0, f1, seg, tut.fps),
                        f"Rendering segment {sid!r}", attempts=3, when=anything,
                        on_retry=on_retry)  # fmt: skip
            times[sid] = round(time.monotonic() - t, 1)
        await save(f"seg-{seg.stem}.mp4", seg)
        done += 1
        progress(done, len(todo))

    t_seg = time.monotonic()
    await asyncio.gather(*(one(*j) for j in todo))
    t_seg = time.monotonic() - t_seg
    (out / "concat.txt").write_text("".join(f"file '{s.resolve()}'\n" for s in segs))
    video, sound = out / "video.mp4", out / "audio.m4a"
    await _run("ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
               "-i", str(out / "concat.txt"), "-c", "copy", str(video))  # fmt: skip
    await _mix_audio(tut, work / "public" / tut.id / "audio", total, sound)
    final = out / "tutorial.mp4"
    await _run("ffmpeg", "-y", "-loglevel", "error", "-i", str(video), "-i", str(sound),
               "-map", "0:v", "-map", "1:a", "-c", "copy", "-shortest",
               "-movflags", "+faststart", str(final))  # fmt: skip
    thumb = out / "thumbnail.png"
    title_at = min(2.6, tut.states[0].duration - 0.1) if tut.states else 0
    await _run("ffmpeg", "-y", "-loglevel", "error", "-ss", f"{title_at:.2f}", "-i", str(final),
               "-frames:v", "1", str(thumb))  # fmt: skip
    captions = out / "captions.srt"
    captions.write_text(srt(tut))
    stats = {
        "duration": round(total, 2),
        "states": len(tut.states),
        "rendered": len(todo),
        "segment_seconds": times,
        "segments_wall_seconds": round(t_seg, 1),
        "render_seconds": round(time.monotonic() - t_start, 1),
    }
    log.info("rendered", extra=fields(**{k: v for k, v in stats.items() if k != "segment_seconds"}))
    return {"video": final, "captions": captions, "thumbnail": thumb, "stats": stats}
