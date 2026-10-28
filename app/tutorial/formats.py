"""Content layer: which kind of video the user wants (tutorial, reel or explainer) and
how long, then a storyboard written with that format's skill (`app/skills/*.md`) before
the step planner runs. Skills describe how a format is delivered (hooks, pacing, what
is fast-forwarded), never the steps of a particular app."""

import re
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.jobs import JobContext

# chat format -> planner mode
FORMATS = {"tutorial": "tutorial", "reel": "influencer", "explainer": "explainer"}
NAMES = {"tutorial": "tutorial", "influencer": "reel", "explainer": "explainer"}
CHOICES = {
    "Tutorial: a step-by-step guide": "tutorial",
    "Reel / short content: hook, surprising facts, comparisons": "influencer",
    "Explainer: a general video about it": "explainer",
}
LENGTHS = {"tutorial": (60, 90, 180), "influencer": (30, 60, 90), "explainer": (60, 90, 120)}
PACE = {"real": "real time", "fast": "fast-forward", "cut": "cut to the result"}
SKILLS = Path(__file__).resolve().parents[1] / "skills"
TIMEOUT = 150


def skill(mode: str) -> str:
    return (SKILLS / f"{NAMES.get(mode, 'tutorial')}.md").read_text().strip()


def length(seconds: int) -> str:
    return f"{seconds} seconds" if seconds < 120 else f"{seconds // 60} minutes"


async def clarify(ctx: JobContext, fmt: str | None, seconds: int | None) -> tuple[str, int]:
    """(planner mode, target seconds), asking the user for whatever the request didn't say."""
    mode = FORMATS.get(fmt or "")
    if mode is None:
        ans = await ctx.ask("What kind of video should this be?", list(CHOICES), key="format")
        mode = CHOICES[ans]
    if not seconds:
        opts = [length(s) for s in LENGTHS[mode]]
        ans = await ctx.ask(f"How long should the {NAMES[mode]} be?", opts, key="length")
        seconds = LENGTHS[mode][opts.index(ans)]
    return mode, max(15, min(300, int(seconds)))


class Beat(BaseModel):
    purpose: Literal["hook", "context", "fact", "comparison", "step", "result", "payoff"]
    seconds: float = Field(description="Seconds of video for this beat")
    on_screen: str = Field(
        description="What is on screen: the page or section, the command and its output, "
        "or the app state"
    )
    line: str = Field(description="What the narration says here, in a few words")
    fact: str = Field(description="The exact fact or number from SOURCE this beat uses, or ''")
    pace: Literal["real", "fast", "cut"] = Field(
        description="'real' time for moments that matter, 'fast' for typing, scrolling, "
        "installs and waits, 'cut' to jump past page loads and long waits"
    )


class Storyboard(BaseModel):
    angle: str = Field(description="The video's angle in one sentence")
    beats: list[Beat]
    caption: str = Field(description="Post caption, 1-3 short lines, facts from SOURCE only")
    hashtags: list[str] = Field(description="3-8 relevant hashtags without '#'")


BOARD = """You are the content director of Cueframe, which records real runs in a Linux
desktop sandbox (terminal and Chromium) and edits them into narrated 16:9 videos.
Storyboard a {name} of about {seconds} seconds for the request, using ONLY the SOURCE.

{skill}

Rules:
- 4-8 beats in viewing order; their `seconds` add up to about {seconds}.
- Every beat shows something the sandbox can really show: a web page or a section of it,
  a command and its output, an app window. No stock footage, faces or anything off-screen.
  There is no code editor or IDE: files are written and run from the terminal.
- `on_screen` names only headings, sections, links and labels that appear in SOURCE
  (pages often lack an "Overview" or "Method" section); otherwise describe the area,
  e.g. "the abstract below the title".
- `fact`: the exact fact or number from SOURCE the beat uses, with its date or source
  when it has one; '' when the beat states no fact. Never use facts from memory.
- `pace`: 'real' for the moments that matter, 'fast' for typing, scrolling, installs and
  waits (shown sped up with a fast-forward badge), 'cut' for page loads and long waits.
  Everything still runs for real; pace only changes the edit.
- caption: a post caption for the video (1-3 short lines) built on the angle and facts;
  no scores, outputs or results of the run itself (it hasn't run yet);
  hashtags: 3-8 relevant tags without '#'."""


async def storyboard(
    ctx: JobContext, goal: str, ref: str, mode: str, seconds: int
) -> Storyboard | None:
    """Map the researched facts into beats with the format's skill; None if it fails
    (the planner then works from the research alone)."""
    ctx.phase("storyboard", f"Storyboarding the {NAMES[mode]}")
    try:
        sb, _ = await ctx.llm.parse(
            Storyboard, purpose="content.storyboard", model=ctx.settings.research_model,
            instructions=BOARD.format(name=NAMES[mode], seconds=seconds, skill=skill(mode)),
            input=f"Request: {goal}\n\nSOURCE:\n{ref}", timeout=TIMEOUT,
            reasoning_effort="medium",
        )  # fmt: skip
    except Exception as exc:
        ctx.thinking("Storyboard", f"skipped ({str(exc)[:160]})")
        return None
    if not sb.beats:
        return None
    seen = [goal, ref, sb.angle, sb.caption, *(f"{b.on_screen} {b.line}" for b in sb.beats)]
    sb.hashtags = hashtags(sb.hashtags, " ".join(seen))
    ctx.thinking("Storyboard", f"{len(sb.beats)} beats: {sb.angle}")
    return sb


_COMMON = (
    "ai ml llm llms tech dev devs tips ideas idea content creator creators trend trends "
    "trending now today daily news new learn learning coding code tutorial tutorials howto "
    "how to explained explainer research paper papers science open source github python "
    "machine deep data model models vision language agents agent tools tool fact facts "
    "shorts reels viral video videos build software programming web search google world "
    "nlp classification text deeplearning datascience opensource machinelearning"
)
COMMON_TAGS = set(_COMMON.split())


def _spelled(tag: str, vocab: set[str]) -> bool:
    """True when `tag` splits into known words (or numbers), e.g. 'googletrends'."""
    t = tag.lower()
    ok = [True] + [False] * len(t)
    for i in range(1, len(t) + 1):
        for j in range(max(0, i - 24), i):
            w = t[j:i]
            if ok[j] and (w.isdigit() or (len(w) > 1 and w in vocab) or w == "a"):
                ok[i] = True
                break
    return ok[-1]


def _respelled(tag: str, vocab: set[str]) -> str | None:
    """The tag itself when it is spelled from known words, else the same tag with one
    doubled letter removed when that is ('googlettrends' -> 'googletrends'), else None."""
    if _spelled(tag, vocab):
        return tag
    for i in range(1, len(tag)):
        if tag[i].lower() == tag[i - 1].lower() and _spelled(t := tag[:i] + tag[i + 1 :], vocab):
            return t
    return None


def hashtags(tags: list[str], text: str) -> list[str]:
    """Clean hashtags: plain characters, no repeats (any case), and each one spelled from
    words in the request, source or storyboard or common post words; a typo is fixed when
    it's a doubled letter and the tag dropped otherwise."""
    vocab = COMMON_TAGS | {w for w in re.findall(r"[a-z]+", text.lower()) if len(w) > 1}
    out: dict[str, str] = {}
    for h in tags:
        t = re.sub(r"[^\w]", "", h).strip("_")
        if t and (t := _respelled(t, vocab)) and t.lower() not in out:
            out[t.lower()] = t
    return list(out.values())[:8]


def board_text(sb: Storyboard) -> str:
    """The storyboard as the step planner reads it."""
    rows = [
        f"{i}. [{b.purpose}, about {b.seconds:g} s, pace {b.pace}] show: {b.on_screen} | "
        f"say: {b.line}" + (f" | fact: {b.fact}" if b.fact else "")
        for i, b in enumerate(sb.beats, 1)
    ]
    return (
        "STORYBOARD: plan steps that show these beats in this order and give each step the "
        "pace of its beat; a beat with nothing to run goes into the intro or outro.\n"
        f"ANGLE: {sb.angle}\n" + "\n".join(rows)
    )


def _cell(s: str) -> str:
    return s.replace("|", "/").replace("\n", " ").strip()


def describe(sb: Storyboard, mode: str, seconds: int) -> str:
    """The storyboard as a chat message."""
    rows = "\n".join(
        f"| {i} | {b.purpose} | {_cell(b.on_screen)} | {PACE[b.pace]} | {b.seconds:g} s |"
        for i, b in enumerate(sb.beats, 1)
    )
    return (
        f"**Storyboard: {NAMES[mode]}, about {length(seconds)}**\n\n{sb.angle}\n\n"
        f"| # | Beat | On screen | Shown | Length |\n|---|---|---|---|---|\n{rows}"
    )


def social(sb: dict[str, Any]) -> str:
    tags = " ".join(f"#{t}" for t in sb.get("hashtags") or [])
    return f"**Caption**\n\n{sb['caption'].strip()}\n\n**Hashtags:** {tags}".strip()
