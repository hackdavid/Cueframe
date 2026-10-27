"""Render a captured tutorial to a YouTube-ready MP4.

Each state is rendered as its own short, muted segment by Remotion, in parallel and
cached by a hash of the state, so an edit re-renders only the states it touched.
Segments are joined without re-encoding; the soundtrack (narration, SFX, music
ducked under the voice) is mixed separately and loudness-normalised to -14 LUFS.

    uv run python scripts/render_tutorial.py tutorials/nginx-website build/nginx-website
"""

import asyncio
import hashlib
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

from app.tutorial.build import build, srt

ROOT = Path(__file__).resolve().parent.parent
RENDERER = ROOT / "renderer"
PARALLEL = 4


def run(cmd: list[str], **kw) -> None:  # type: ignore[no-untyped-def]
    subprocess.run(cmd, check=True, **kw)


def stage_assets(run_dir: Path, base: str) -> None:
    # Remotion only serves real files under public/ (symlinks break), so copy.
    pub = RENDERER / "public" / base
    (pub / "audio").mkdir(parents=True, exist_ok=True)
    for f in [run_dir / "recording.mp4", *(run_dir / "audio").glob("*.mp3")]:
        dst = pub / f.relative_to(run_dir)
        if not dst.exists() or dst.stat().st_mtime < f.stat().st_mtime:
            shutil.copy2(f, dst)


AF = "aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo"


async def render_segment(
    bundle: Path, props: Path, a: int, b: int, out: Path, sem: asyncio.Semaphore
) -> float:
    async with sem:
        t = time.monotonic()
        proc = await asyncio.create_subprocess_exec(
            "npx", "remotion", "render", str(bundle), "Tutorial", str(out), f"--props={props}",
            f"--frames={a}-{b}", "--muted", "--concurrency=2", "--codec=h264", "--crf=16",
            "--pixel-format=yuv420p", "--log=error",
            cwd=RENDERER,
        )  # fmt: skip
        if await proc.wait():
            raise RuntimeError(f"segment {out.name} failed")
        return time.monotonic() - t


def mix_audio(tut, run_dir: Path, total: float, out: Path) -> None:  # type: ignore[no-untyped-def]
    audio = run_dir / "audio"
    vol = {"click": 0.45, "keys": 0.22, "whoosh": 0.35, "success": 0.4, "pop": 0.35}
    voice, fx, inputs = [], [], []
    t0 = 0.0
    for st in tut.states:
        for n in st.narration:
            inputs += ["-i", str(audio / Path(n.audio_url).name)]
            voice.append((_count(inputs) - 1, t0 + n.at, 1.0, None))
        for s in st.sfx:
            inputs += ["-stream_loop", "-1", "-i", str(audio / f"sfx_{s.kind}.mp3")]
            fx.append((_count(inputs) - 1, t0 + s.t, vol[s.kind], s.duration or 1.6))
        t0 += round(st.duration * tut.fps) / tut.fps
    inputs += ["-stream_loop", "-1", "-i", str(audio / "sfx_music.mp3")]
    music = _count(inputs) - 1
    parts, vlabels, flabels = [], [], []
    for i, (idx, at, v, _) in enumerate(voice):
        ms = int(at * 1000)
        parts.append(f"[{idx}:a]{AF},adelay={ms}|{ms},volume={v}[v{i}]")
        vlabels.append(f"[v{i}]")
    for i, (idx, at, v, d) in enumerate(fx):
        ms = int(at * 1000)
        parts.append(
            f"[{idx}:a]atrim=0:{d:.2f},afade=t=out:st={max(0, d - 0.12):.2f}:d=0.12,aresample=48000,"
            f"adelay={ms}|{ms},volume={v}[f{i}]"
        )
        flabels.append(f"[f{i}]")
    parts.append(f"{''.join(vlabels)}amix=inputs={len(vlabels)}:normalize=0,asplit=2[voice][key]")
    parts.append(f"{''.join(flabels)}amix=inputs={len(flabels)}:normalize=0[fx]")
    parts.append(
        f"[{music}:a]atrim=0:{total:.2f},{AF},volume=0.28,afade=t=in:d=1.5,"
        f"afade=t=out:st={total - 3:.2f}:d=3[mus]"
    )
    parts.append("[mus][key]sidechaincompress=threshold=0.02:ratio=8:attack=20:release=400[duck]")
    parts.append(
        f"[voice][fx][duck]amix=inputs=3:normalize=0,apad,atrim=0:{total:.3f},"
        "loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000[out]"
    )
    run(["ffmpeg", "-y", "-loglevel", "error", *inputs, "-filter_complex", ";".join(parts),
         "-map", "[out]", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", str(out)])  # fmt: skip


def _count(inputs: list[str]) -> int:
    return inputs.count("-i")


async def main(spec_dir: Path, run_dir: Path) -> None:
    t_start = time.monotonic()
    base = spec_dir.name
    run_dir = run_dir.resolve()
    tut = build(spec_dir, run_dir, base)
    out = run_dir / "render"
    seg_dir = out / "segments"
    seg_dir.mkdir(parents=True, exist_ok=True)
    props = out / "props.json"
    props.write_text(json.dumps({"tutorial": tut.model_dump()}))
    (out / "tutorial.json").write_text(tut.model_dump_json(indent=1))
    stage_assets(run_dir, base)
    bundle = out / "bundle"
    shutil.rmtree(bundle, ignore_errors=True)
    run(
        ["npx", "remotion", "bundle", "src/index.ts", f"--out-dir={bundle}", "--log=error"],
        cwd=RENDERER,
    )
    code = hashlib.sha256(
        b"".join(p.read_bytes() for p in sorted((RENDERER / "src").glob("*")))
    ).hexdigest()[:8]
    sem = asyncio.Semaphore(PARALLEL)
    jobs, segs, a = [], [], 0
    for st in tut.states:
        n = round(st.duration * tut.fps)
        h = hashlib.sha256(
            (code + st.model_dump_json() + json.dumps(tut.steps)).encode()
        ).hexdigest()[:12]
        seg = seg_dir / f"{a:05d}_{st.id}_{h}.mp4"
        segs.append(seg)
        if not seg.exists():
            jobs.append((st.id, render_segment(bundle, props, a, a + n - 1, seg, sem)))
        a += n
    total = a / tut.fps
    print(f"{len(tut.states)} states, {total:.1f}s; rendering {len(jobs)} changed segments")
    times = await asyncio.gather(*(j for _, j in jobs))
    for (sid, _), t in zip(jobs, times, strict=True):
        print(f"  {sid}: {t:.1f}s")
    (out / "concat.txt").write_text("".join(f"file '{s.resolve()}'\n" for s in segs))
    video = out / "video.mp4"
    run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(out / "concat.txt"),
            "-c",
            "copy",
            str(video),
        ]
    )
    sound = out / "audio.m4a"
    mix_audio(tut, run_dir, total, sound)
    final = out / f"{base}.mp4"
    run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(video), "-i", str(sound), "-map", "0:v", "-map", "1:a",
         "-c", "copy", "-shortest", "-movflags", "+faststart", str(final)])  # fmt: skip
    (out / f"{base}.srt").write_text(srt(tut))
    print(f"wrote {final} in {time.monotonic() - t_start:.0f}s")


if __name__ == "__main__":
    asyncio.run(main(Path(sys.argv[1]), Path(sys.argv[2])))
