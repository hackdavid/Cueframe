"""Automatic checks of what the pipeline produced, so problems are fixed before the
user sees them. `recording` checks the raw desktop capture (did the screen really get
recorded for every step?); `final` checks the published MP4 and captions."""

import json
import re
from pathlib import Path
from typing import Any

from app import proc
from app.tutorial.contracts import Tutorial

MIN_DB = -45.0  # mean loudness below this means the soundtrack is effectively silent


async def _out(*cmd: str) -> str:
    return await proc.out(*cmd)


async def probe(path: Path) -> dict[str, Any]:
    raw = await _out("ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json",
                     str(path))  # fmt: skip
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def duration(info: dict[str, Any]) -> float:
    try:
        return float(info["format"]["duration"])
    except (KeyError, TypeError, ValueError):
        return 0.0


async def black_spans(path: Path, start: float = 0, length: float | None = None) -> list[tuple]:
    """Spans (from `start`) where every pixel is black: nothing was captured."""
    cut = ["-ss", f"{start:.2f}"] + (["-t", f"{length:.2f}"] if length else [])
    log = await _out("ffmpeg", "-hide_banner", *cut, "-i", str(path), "-an", "-vf",
                     "blackdetect=d=0.5:pix_th=0.0:pic_th=0.999", "-f", "null", "-")  # fmt: skip
    return [
        (float(a), float(b)) for a, b in re.findall(r"black_start:([\d.]+) black_end:([\d.]+)", log)
    ]


async def mean_volume(path: Path) -> float:
    log = await _out("ffmpeg", "-hide_banner", "-i", str(path), "-vn", "-af", "volumedetect",
                     "-f", "null", "-")  # fmt: skip
    m = re.search(r"mean_volume: (-?[\d.]+) dB", log)
    return float(m.group(1)) if m else -99.0


async def recording(path: Path, records: list[dict[str, Any]]) -> list[str]:
    """Problems with the raw capture of the steps shown in the video."""
    info = await probe(path)
    got = duration(info)
    shown = [r for r in records if r.get("in_video")]
    if not got:
        return ["the screen recording is missing or unreadable"]
    need = max((r["t_out"] for r in shown), default=0)
    if got + 0.5 < need:
        return [f"the screen recording stops at {got:.1f} s but the steps run to {need:.1f} s"]
    out = []
    for r in shown:
        spans = await black_spans(path, r["t_in"], r["t_out"] - r["t_in"])
        if spans:
            out.append(f"the screen was blank while recording step {r['step']!r}")
    return out


async def final(tut: Tutorial, video: Path, captions: Path, expect: float) -> list[str]:
    """Problems with the finished MP4 and captions."""
    info = await probe(video)
    streams = info.get("streams") or []
    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    out = []
    if not v:
        return ["the video has no picture"]
    if (v.get("width"), v.get("height")) != (tut.width, tut.height):
        out.append(f"the video is {v.get('width')}x{v.get('height')}, not {tut.width}x{tut.height}")
    got = duration(info)
    if abs(got - expect) > 0.5:
        out.append(f"the video is {got:.1f} s long, expected {expect:.1f} s")
    if not a:
        out.append("the video has no sound track")
    elif (db := await mean_volume(video)) < MIN_DB:
        out.append(f"the sound track is silent ({db:.0f} dB)")
    text = captions.read_text(errors="replace") if captions.exists() else ""
    if "-->" not in text:
        out.append("the captions file is empty")
    return out
