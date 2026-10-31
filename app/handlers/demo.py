"""Placeholder long-running job that exercises the whole pipeline: phases,
progress, a real ffmpeg render, and a markdown reply with image + video.
Replace with the real agent pipeline in later phases."""

import asyncio
import shutil
from typing import Any

from app import proc
from app.jobs import JobContext, job_handler

PHASES = [
    ("research", "Researcher", "Reading the request"),
    ("plan", "Planner", "Drafting a plan"),
    ("execute", "Operator", "Rendering a preview clip"),
    ("stitch", "Editor", "Assembling the reply"),
]


async def _render_clip(ctx: JobContext, text: str) -> dict[str, str] | None:
    if not ctx.settings.demo_render_video or not shutil.which("ffmpeg"):
        return None
    out = ctx.artifact_dir()
    video, poster = out / "preview.mp4", out / "poster.png"
    caption = "".join(c for c in text[:40] if c.isalnum() or c in " -_.,!?") or "Cueframe"
    caption = caption.replace(":", " ").replace("'", " ")
    rc, _ = await proc.raw(
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30:duration=5",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=5",
        "-vf", f"drawtext=text='{caption}':fontcolor=white:fontsize=48:box=1:"
               "boxcolor=black@0.6:boxborderw=20:x=(w-text_w)/2:y=h-120",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
        "-movflags", "+faststart", str(video),
    )  # fmt: skip
    if rc != 0:
        return None
    await proc.raw(
        "ffmpeg", "-y", "-loglevel", "error", "-ss", "2", "-i", str(video),
        "-frames:v", "1", "-vf", "scale=640:-1", str(poster),
    )  # fmt: skip
    return {"video": ctx.artifact_url(video), "poster": ctx.artifact_url(poster)}


def _embed(att: dict[str, Any]) -> str:
    name = att["filename"].replace("[", "(").replace("]", ")")
    if att["kind"] in ("image", "video"):
        return f"![{name}]({att['url']})"
    return f"[{name}]({att['url']})"


@job_handler("demo.reply")
async def demo_reply(ctx: JobContext) -> dict[str, Any]:
    text: str = ctx.input.get("text", "")
    attachments: list[dict[str, Any]] = ctx.input.get("attachments", [])
    step = ctx.settings.demo_job_seconds / len(PHASES)
    clip = None
    for i, (phase, role, label) in enumerate(PHASES):
        ctx.phase(phase, label)
        ctx.thinking(role, label)
        if phase == "execute":
            clip = await _render_clip(ctx, text)
        ticks = 4
        for t in range(ticks):
            await asyncio.sleep(step / ticks)
            ctx.progress((i + (t + 1) / ticks) / len(PHASES), label)

    lines = [
        "### Background job finished",
        "",
        f"You said: *{text.strip() or '(no text)'}*",
        "",
        "| Phase | Status |",
        "|---|---|",
        *[f"| {label} | done |" for _, _, label in PHASES],
    ]
    if attachments:
        lines += ["", "#### Your files", ""]
        lines += [
            f"- **{a['filename']}** · {a['kind']} · {a['size'] / 1024:.0f} KB" for a in attachments
        ]
        lines += ["", *[_embed(a) for a in attachments if a["kind"] in ("image", "video")]]
    if clip:
        lines += [
            "",
            "#### Rendered preview",
            "",
            f"![preview clip]({clip['video']})",
            "",
            f"Poster frame: ![poster]({clip['poster']})",
        ]
    lines += ["", "```json", f'{{"status": "ok", "phases": {len(PHASES)}}}', "```"]
    ctx.phase("done", "Done")
    ctx.reply("\n".join(lines))
    return {"clip": clip}
