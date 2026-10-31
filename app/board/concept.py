"""Concept boards: papers, models and ideas explained by drawing block diagrams, before/after
panels, quotes and the source's own result charts on the same endless board as the maths
lessons (same render path, voice and 16:9 camera). Every number drawn or said must appear in
the source facts."""

import re
import time
from decimal import Decimal
from typing import Any

import httpx
from pydantic import BaseModel, Field

from app import languages
from app.board import timeline, tutor
from app.board.contracts import BoardPlan, normalize, problems
from app.jobs import JobContext, JobFailed
from app.llm import LLMError
from app.log import fields, get_logger
from app.tutorial import dedup, formats

log = get_logger("concept")
TRIES = 3
CRAMPED = "almost touch, so the arrow can't be seen"  # contracts.problems wording
PLAN_TIMEOUT = 120  # a plan takes 30-50 s; a call still open at 120 s is stuck, retry it
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


def undrawn(plan: BoardPlan) -> list[str]:
    """An explainer has to draw the idea: at least one block diagram."""
    kinds = {op.kind for st in plan.steps for op in st.ops}
    if {"node", "link"} <= kinds:
        return []
    return ["no block diagram: draw the key idea as a figure with 2-4 `node` ops joined by "
            "`link` ops"]  # fmt: skip


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
FILL = 0.85  # least share of the asked length, so a 60 s request is not a 45 s video
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
    if words < (low := word_limit(int(seconds * FILL), len(plan.steps))):
        return [f"the narration is {words:.0f} words, too short for {seconds} s with "
                f"{len(plan.steps)} steps; lengthen it to {low}-{limit} words"]  # fmt: skip
    if words <= limit:
        return []
    return [f"the narration is {words:.0f} words, too long for {seconds} s with "
            f"{len(plan.steps)} steps; cut it to at most {limit} words"]  # fmt: skip


async def plan(ctx: JobContext, source: str, mode: str, seconds: int | None,
               language: str | None) -> BoardPlan:  # fmt: skip
    want = (
        f"The video must be about {seconds} s: {word_limit(int(seconds * FILL), 6)}-"
        f"{word_limit(seconds, 8)} words of narration in total across 6-8 steps."
        if seconds
        else "Aim for 50-80 s."
    )
    base = f"{want}\nSource facts (use only these):\n{source}" + languages.instruction(language)
    feedback, issues, best = "", [], None
    for attempt in range(1, TRIES + 1):
        ctx.thinking("Explainer", "Drawing the idea" if attempt == 1 else "Fixing the board")
        try:
            p, _ = await ctx.llm.parse(
                BoardPlan, purpose="concept.plan", model=ctx.settings.research_model,
                instructions=skill(mode), input=base + feedback, reasoning_effort="medium",
                timeout=PLAN_TIMEOUT,
            )  # fmt: skip
        except LLMError as exc:  # one malformed answer shouldn't end the job: plan again
            log.warning(
                "concept plan unreadable", extra=fields(attempt=attempt, error=str(exc)[:300])
            )
            ctx.reply(
                f"**Board plan {attempt}:** the answer wasn't a readable plan; planning again."
            )
            continue
        if not p.feasible:
            raise JobFailed(f"I can't draw that as a board: {p.reason}", {"planned": False})
        p.level = "undergrad"
        issues = (problems(normalize(p)) + undrawn(p) + unsupported(p, source)
                  + too_long(p, seconds))  # fmt: skip
        if dups := dedup.duplicates(dedup.say_lines(p)):
            if dedup.enforce():
                issues += dups
            else:
                log.info("narration repeat (shadow)", extra=fields(pairs=dups[:3]))
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
        # Repair from the best plan so far, never from a worse attempt; a plan with no
        # diagram isn't worth repairing (repairs of it degrade further), so plan afresh.
        if undrawn(best[0]):
            feedback = ""
            continue
        feedback = (
            f"\n\nYour previous plan:\n{best[0].model_dump_json(exclude_defaults=True)}\n\n"
            "Fix only these problems and return the whole plan. Keep every other op exactly "
            "as it is, with the same kind, id, text and points. Never fix a problem by deleting "
            "nodes, links, quotes or bars:\n- " + "\n- ".join(best[1])
        )
    if best and all(CRAMPED in i for i in best[1]):  # short arrows beat no video
        log.warning("concept plan with short arrows", extra=fields(issues=best[1][:3]))
        ctx.reply("Using the best plan; some arrows between close boxes are short.")
        return best[0]
    issues = best[1] if best else issues
    raise JobFailed(f"The board still had problems: {'; '.join(issues[:5])}", {"issues": issues})


class Narration(BaseModel):
    says: list[str] = Field(description="The new narration of each step, in order")


SCENE = (
    "\n\nThis plan is ONE scene inside a bigger video, not a whole explainer: 1-2 "
    "steps, one diagram (a node/link block diagram, a bars chart or a quote card), 1-3 "
    "spoken sentences in total, about 8 seconds. No recap. The scene explains exactly "
    "this: "
)


async def scene(ctx: JobContext, topic: str, source: str, mode: str = "explainer",
                language: str | None = None) -> BoardPlan:  # fmt: skip
    """A one-diagram board plan embedded as a scene in a tutorial video. Validated like
    a full concept board (structure + every number traced to the source)."""
    base = (f"Draw the scene.\n\nSource facts (use only these):\n{source}"
            + languages.instruction(language))  # fmt: skip
    feedback, issues = "", ["unset"]
    for _ in range(2):
        p, _ = await ctx.llm.parse(
            BoardPlan, purpose="concept.scene", model=ctx.settings.research_model,
            instructions=skill(mode) + SCENE + topic, input=base + feedback,
            reasoning_effort="low", timeout=PLAN_TIMEOUT,
        )  # fmt: skip
        p.level = "undergrad"
        issues = problems(normalize(p)) + unsupported(p, source)
        if not issues:
            return p
        feedback = ("\n\nYour previous plan had problems; fix them and return the whole "
                    "plan:\n- " + "\n- ".join(issues))  # fmt: skip
    raise JobFailed(f"The board scene still had problems: {'; '.join(issues[:3])}",
                    {"issues": issues})  # fmt: skip


async def stretch(ctx: JobContext, p: BoardPlan, tl: dict, seconds: int | None, source: str,
                  mode: str, language: str | None) -> BoardPlan | None:  # fmt: skip
    """The voice's real pace varies (1.7-2.3 words a second), so a plan that fit the word
    budget can still come out short. Measured on the recorded voice, rewrite only the
    narration to fill the asked length; None when it already fits or the rewrite won't do."""
    if not seconds or tl["seconds"] >= seconds * FILL:
        return None
    audio = sum(st["audio"] for st in tl["steps"])
    rate = sum(len(st["words"]) for st in tl["steps"]) / max(audio, 1.0)
    have = sum(spoken_words(st.say) for st in p.steps)
    want = int(have * (seconds - (tl["seconds"] - audio)) / max(audio, 1.0))
    ctx.reply(f"The narration came out at {tl['seconds']:.0f} s for {seconds} s "
              f"({rate:.1f} words a second); lengthening it to about {want} words.")  # fmt: skip
    r, _ = await ctx.llm.parse(
        Narration, purpose="concept.stretch", model=ctx.settings.research_model,
        instructions=skill(mode), reasoning_effort="low", timeout=PLAN_TIMEOUT,
        input=f"Rewrite the narration of this {len(p.steps)}-step board to {int(want * 0.9)}-"
        f"{want} words in total (now {have:.0f}), so it fills {seconds} s; never more. Keep "
        "each step about what that step draws and its numbers exactly as written. Add only "
        "facts from the source, each with the source's own conditions (which model, setting, "
        "hardware or comparison it holds for); never widen a claim, e.g. 'highest of the "
        "compared methods' must not become 'the highest'. Write it to be spoken: no code "
        "names, settings or symbols like max_tokens, top_p or ≥."
        + languages.instruction(language)
        + "\n\nSteps:\n" + "\n".join(f"{i}. {st.say}" for i, st in enumerate(p.steps, 1))
        + f"\n\nSource facts (use only these):\n{source}",
    )  # fmt: skip
    if len(r.says) != len(p.steps):
        return None
    q = p.model_copy(deep=True)
    for st, say in zip(q.steps, r.says, strict=True):
        st.say = say
    if issues := unsupported(q, source):
        log.warning("stretched narration dropped", extra=fields(issues=issues[:3]))
        return None
    if dups := dedup.duplicates(dedup.say_lines(q)):
        log.warning("stretched narration repeated a line", extra=fields(dups=dups[:3]))
        return None
    return q


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
        fit=lambda q, tl: stretch(ctx, q, tl, seconds, source, mode, language),
    )
