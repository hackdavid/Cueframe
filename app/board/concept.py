"""Concept boards: papers, models and ideas explained by drawing block diagrams, before/after
panels, quotes and the source's own result charts on the same endless board as the maths
lessons (same render path, voice and 16:9 camera). Every number drawn or said must appear in
the source facts."""

import re
import time
from decimal import Decimal
from typing import Any

import httpx

from app import languages
from app.board import timeline, tutor
from app.board.contracts import BoardPlan, normalize, problems
from app.jobs import JobContext, JobFailed
from app.tutorial import formats

TRIES = 3
SMALL = {"0", "1", "2", "3", "4", "5"}  # diagram counts like "3 steps"
_NUM = re.compile(r"(?<![\w.,])(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?![\d,]*\d)")


CARD_CHARS = 6000


async def model_card(url: str) -> str:
    """The Hugging Face model card text (benchmarks, architecture) as extra source facts."""
    m = re.match(r"https://huggingface\.co/([\w.-]+/[\w.-]+)/?$", url or "")
    if not m:
        return ""
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as http:
            r = await http.get(f"https://huggingface.co/{m.group(1)}/raw/main/README.md")
            r.raise_for_status()
    except httpx.HTTPError:
        return ""
    text = re.sub(r"\A---\n.*?\n---\n", "", r.text, flags=re.S)
    text = re.sub(r"<[^>]+>|!\[[^\]]*\]\([^)]*\)", "", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()[:CARD_CHARS]
    return (
        f"\n\nModel card (its own words; facts may be taken from here too):\n{text}" if text else ""
    )


def skill(mode: str) -> str:
    base = (formats.SKILLS / "concept.md").read_text().strip()
    if mode == "influencer":
        base += (
            "\n\nThis is a reel: open with a bold one-line hook, 5-7 steps, short "
            "energetic sentences, end with a call to follow for more."
        )
    return base


def _nums(s: str) -> set[str]:
    """Numbers in canonical form, so "90.20%", "90.2" and "1,000" match their source."""
    return {_canon(n.replace(",", "")) for n in _NUM.findall(s)}


def _canon(n: str) -> str:
    """Exact decimal form without trailing zeros: "90.20" -> "90.2", "1000" stays."""
    d = Decimal(n)
    return format(d.normalize() if d != d.to_integral() else d.to_integral(), "f")


def unsupported(plan: BoardPlan, source: str) -> list[str]:
    """Numbers on the board or in the narration that the source never states."""
    have = _nums(source)
    out = [
        f"title/problem/recap: the number {n} is not in the source facts"
        for n in sorted(_nums(" ".join([plan.title, plan.problem, *plan.recap])) - have)
        if n not in SMALL
    ]
    for si, st in enumerate(plan.steps, 1):
        seen = " ".join([st.say, *(f"{o.text} {o.latex} {' '.join(o.labels)}" for o in st.ops)])
        vals = {_canon(repr(v)) for o in st.ops for v in o.values}
        for n in sorted((_nums(seen) | vals) - have):
            if n not in SMALL:
                out.append(f"step {si}: the number {n} is not in the source facts")
    return out


WORDS_PER_SECOND = 1.7  # measured on the board voice, numbers read out in full
_UNSPACED = re.compile(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af\u0e00-\u0e7f]")


def spoken_words(say: str) -> float:
    """Word-equivalents to speak: scripts written without spaces count ~2 characters a word."""
    unspaced = len(_UNSPACED.findall(say))
    return len(_UNSPACED.sub(" ", say).split()) + unspaced / 2


def word_limit(seconds: int, steps: int) -> int:
    """Words of narration that fit `seconds` after the board's intro, outro and pauses."""
    speech = seconds - timeline.INTRO - timeline.OUTRO - timeline.GAP * steps
    return max(int(speech * WORDS_PER_SECOND), 20)


def too_long(plan: BoardPlan, seconds: int | None) -> list[str]:
    """The narration must fit the length asked for, after the board's intro, outro and pauses."""
    if not seconds:
        return []
    words = sum(spoken_words(st.say) for st in plan.steps)
    limit = word_limit(seconds, len(plan.steps))
    if words <= limit:
        return []
    return [f"the narration is {words:.0f} words, too long for {seconds} s with "
            f"{len(plan.steps)} steps; cut it to at most {limit} words"]  # fmt: skip


async def plan(ctx: JobContext, source: str, mode: str, seconds: int | None,
               language: str | None) -> BoardPlan:  # fmt: skip
    want = (
        f"The video must be about {seconds} s: at most {word_limit(seconds, 8)} words of "
        "narration in total across 6-8 steps."
        if seconds
        else "Aim for 50-80 s."
    )
    base = f"{want}\nSource facts (use only these):\n{source}" + languages.instruction(language)
    feedback, issues, best = "", [], None
    for attempt in range(1, TRIES + 1):
        ctx.thinking("Explainer", "Drawing the idea" if attempt == 1 else "Fixing the board")
        p, _ = await ctx.llm.parse(
            BoardPlan, purpose="concept.plan", model=ctx.settings.research_model,
            instructions=skill(mode), input=base + feedback, reasoning_effort="medium",
        )  # fmt: skip
        if not p.feasible:
            raise JobFailed(f"I can't draw that as a board: {p.reason}", {"planned": False})
        p.level = "undergrad"
        issues = problems(normalize(p)) + unsupported(p, source) + too_long(p, seconds)
        ctx.reply(
            f"**Board plan {attempt}:** {len(p.steps)} steps, "
            + (
                "every number traced to the source."
                if not issues
                else f"{len(issues)} problem(s):\n" + "\n".join(f"- {i}" for i in issues[:8])
            )
        )
        if not issues:
            return p
        if best is None or len(issues) < len(best[1]):
            best = (p, issues)
        # Repair from the best plan so far, never from a worse attempt.
        feedback = (
            f"\n\nYour previous plan:\n{best[0].model_dump_json(exclude_defaults=True)}\n\n"
            "Fix only these problems and return the whole plan. Keep every other op exactly "
            "as it is, with the same kind, id, text and points:\n- " + "\n- ".join(best[1])
        )
    issues = best[1] if best else issues
    raise JobFailed(f"The board still had problems: {'; '.join(issues[:5])}", {"issues": issues})


async def make(ctx: JobContext, source: str, topic: str, mode: str = "explainer",
               seconds: int | None = None) -> dict[str, Any]:  # fmt: skip
    """A drawn explainer (or reel) about `topic` from `source` facts."""
    conv = ctx.store.get_conversation(ctx.conversation_id) if ctx.conversation_id else None
    if not conv or ctx.llm is None or not ctx.llm.configured:
        raise ValueError("concept boards need a conversation and a model")
    t0 = time.monotonic()
    language = (ctx.input.get("job_input") or {}).get("language") or languages.detect(
        ctx.input.get("text")
    )
    ctx.reply("I'll **draw** this on the board: the idea as diagrams, and the results as "
              "a chart of the source's own numbers.")  # fmt: skip
    ctx.phase("plan", "Planning the board")
    p = await plan(ctx, source, mode, seconds, language)
    ctx.reply(f"**{p.title}**\n\n" + tutor.describe(p))
    return await tutor.deliver(
        ctx,
        conv,
        p,
        topic,
        t0,
        language,
        recipe="concept",
        rules=skill(mode),
        validate=lambda q: unsupported(q, source),
    )
