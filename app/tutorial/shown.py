"""Check that each narration line talks about what the recording shows while it plays.
Frames are cut from the real recording; a line about something that isn't on screen is
rewritten from what is (no longer than before), so the voice never describes a view the
viewer can't see. The footage itself is never changed."""

import asyncio
import base64
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from app.heal import Healer, short
from app.jobs import JobContext

FRAMES_AT = (0.35, 0.9)  # where in each step's footage to look
TIMEOUT = 150
SLACK_WORDS = 3
TONE = {
    "tutorial": "tutorial; friendly teacher, says what each step does and why",
    "influencer": "reel; energetic and punchy, speaks to the viewer ('you'), facts with numbers",
    "explainer": "explainer; calm, clear and curious, problem -> idea -> why it matters",
}

SHOWN = """You check that a narrated screen recording shows what its narration says. For each
step you get its narration lines and frames cut from the recording while those lines play.
A line is fine when the frames show what it talks about (the page, section, code, output,
figure or element it names), or when it is a plain transition or call to action. When a
line names or describes something that is not visible in its frames, rewrite it so it
describes what IS visible in them, in the same voice, tense and style, with at most as
many words as the original. Keep the line's job in the video (hook, fact, step, result,
payoff) and the format's tone: tie what is visible to the point the line was making, so
it still tells the viewer why it matters, instead of listing the screen ('Folders and
links fill the page' is a bad rewrite; 'Here's the code: the whole method is open
source' is good when the frames show its repo). Never start with 'I will' or 'Let me'.
Use only facts visible in the frames or stated in the source brief; never add numbers
or claims. Return only the lines that need rewriting (none when
every line matches). Each step's badge is shown on screen when it ends: when it claims
the same unshown thing, give a new 2-4 word badge too."""


class LineFix(BaseModel):
    key: str = Field(description="The narration line's key, e.g. 'open-repo'")
    problem: str = Field(description="What the line says that the frames don't show")
    narration: str = Field(description="The rewritten line, about what the frames show")
    badge: str = Field(
        description="If the step's badge claims the same unshown thing: a new 2-4 word badge "
        "about what the frames show; else ''"
    )


class Shown(BaseModel):
    """LLM contract: narration lines that describe something not on screen."""

    fixes: list[LineFix]


async def _frame(video: Path, t: float) -> dict[str, str] | None:
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-loglevel", "error", "-ss", f"{max(0.0, t):.2f}", "-i", str(video),
        "-frames:v", "1", "-vf", "scale=960:-2", "-q:v", "5", "-f", "image2pipe",
        "-vcodec", "mjpeg", "-", stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )  # fmt: skip
    data, _ = await proc.communicate()
    if proc.returncode != 0 or not data:
        return None
    b64 = base64.b64encode(data).decode()
    return {"type": "input_image", "image_url": f"data:image/jpeg;base64,{b64}"}


def valid(fix: LineFix, lines: dict[str, str], keys: set[str]) -> bool:
    text = fix.narration.strip()
    return (fix.key in keys and bool(text) and text != lines[fix.key].strip()
            and len(text.split()) <= len(lines[fix.key].split()) + SLACK_WORDS)  # fmt: skip


async def check(
    ctx: JobContext, spec: dict[str, Any], records: list[dict[str, Any]], video: Path,
    heal: Healer,
) -> list[str]:  # fmt: skip
    """Rewrite in `spec["lines"]` every step line the footage doesn't show; returns the
    keys that changed (the caller re-voices them). Skipped without a model."""
    if ctx.llm is None or not ctx.llm.configured or not video.exists():
        return []
    by_id = {s["id"]: s for s in spec["steps"]}
    step_of = {k: s for s in spec["steps"] for k in s["narration"]}
    shown = [r for r in records if r.get("in_video") and r.get("ok") and r["step"] in by_id]
    if not shown:
        return []
    at = FRAMES_AT if len(shown) <= 8 else (0.6,)
    lines, keys = spec["lines"], set()
    content: list[dict[str, str]] = [
        {
            "type": "input_text",
            "text": f"Format: {TONE.get(spec.get('mode') or 'tutorial', TONE['tutorial'])}",
        }
    ]
    if spec.get("brief"):
        content.append({"type": "input_text", "text": f"Source brief:\n{spec['brief']}"})
    for r in shown:
        step = by_id[r["step"]]
        said = [k for k in step["narration"] if k in lines]
        keys.update(said)
        text = "\n".join([*(f"{k}: {lines[k]}" for k in said), f"badge: {step.get('badge') or ''}"])
        content.append({"type": "input_text", "text": f"Step {step['label']!r}\n{text}"})
        span = r["t_out"] - r["t_in"]
        for f in at:
            if img := await _frame(video, r["t_in"] + f * span):
                content.append(img)
    try:
        out, _ = await ctx.llm.parse(
            Shown, purpose="tutorial.shown", model=ctx.settings.research_model,
            instructions=SHOWN, input=[{"role": "user", "content": content}],
            timeout=TIMEOUT, reasoning_effort="low",
        )  # fmt: skip
    except Exception as exc:  # a safeguard: a failed call doesn't block the video
        ctx.thinking("Narration check", f"skipped ({short(str(exc))})")
        return []
    changed = []
    for fix in out.fixes:
        if not valid(fix, lines, keys) or fix.key in changed:
            continue
        heal(f"Narration check, **{fix.key}**: {fix.problem.rstrip('.')}. "
             f"Now says \"{fix.narration.strip()}\"")  # fmt: skip
        lines[fix.key] = fix.narration.strip()
        if 0 < len(fix.badge.split()) <= 5 and step_of[fix.key].get("badge"):
            step_of[fix.key]["badge"] = fix.badge.strip()
        changed.append(fix.key)
    return changed
