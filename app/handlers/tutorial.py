"""Tutorial jobs. `assistant.reply` is the default chat job: it reads the message and
either makes a tutorial, edits the conversation's tutorial, or answers normally."""

import re
from typing import Any, Literal

from pydantic import BaseModel

from app.board import tutor
from app.handlers.demo import demo_reply
from app.handlers.research import docs_research
from app.jobs import JobContext, JobManager, job_handler
from app.tutorial import formats, pipeline, recipes


@job_handler("tutorial.make")
async def tutorial_make(ctx: JobContext) -> dict[str, Any]:
    ji = ctx.input.get("job_input") or {}
    rid = ji.get("recipe") or recipes.match(ctx.input["text"])
    mode = ji.get("mode") if ji.get("mode") in ("influencer", "explainer") else "tutorial"
    return await pipeline.make(ctx, rid, goal=ctx.input["text"], mode=mode,
                               trending=bool(ji.get("trending")),
                               seconds=ji.get("seconds"))  # fmt: skip


@job_handler("board.make")
async def board_make(ctx: JobContext) -> dict[str, Any]:
    ji = ctx.input.get("job_input") or {}
    text, level, seconds = ctx.input["text"], ji.get("level"), ji.get("seconds")
    return await tutor.make(ctx, text, level=level, seconds=seconds)


@job_handler("tutorial.edit")
async def tutorial_edit(ctx: JobContext) -> dict[str, Any]:
    ji = ctx.input.get("job_input") or {}
    edits = ji.get("edits")
    return await pipeline.edit(
        ctx,
        ji["tutorial_id"],
        instruction=ctx.input.get("text"),
        edits=None if edits is None else [pipeline.StateEdit.model_validate(e) for e in edits],
    )


@job_handler("tutorial.resume")
async def tutorial_resume(ctx: JobContext) -> dict[str, Any]:
    return await pipeline.resume(ctx, ctx.input["job_input"]["tutorial_id"])


def resume_interrupted(jobs: JobManager, interrupted: list[dict[str, Any]]) -> list[str]:
    """After a restart, continue every tutorial whose job was interrupted mid-way."""
    out = []
    for t in jobs.store.tutorials_to_resume([j["id"] for j in interrupted]):
        payload = {"text": "", "attachments": [], "job_input": {"tutorial_id": t["id"]}}
        out.append(jobs.submit("tutorial.resume", payload, t["conversation_id"])["id"])
    return out


class Route(BaseModel):
    intent: Literal["make_tutorial", "edit_tutorial", "math_tutor", "chat"]
    recipe: str | None = None
    format: Literal["tutorial", "reel", "explainer"] | None = None
    seconds: int | None = None
    trending: bool = False
    level: Literal["child", "school", "undergrad"] | None = None


ROUTER = """You route messages in Cueframe, an app that makes narrated video tutorials
from real runs in a Linux desktop sandbox. Pick:
- make_tutorial: the user wants a (new) tutorial/video/walkthrough of a task, or a
  video about a file they attached or a link they sent (a PDF, slides, a repo). Set
  `recipe` to the id of the matching recipe below, or null if none fits (a new
  tutorial is then planned automatically). Set `format` to what they asked for:
  "tutorial" (a how-to, step-by-step guide), "reel" (a reel, short, TikTok, influencer,
  creator or social content), "explainer" (an explainer or overview video); null when
  the message doesn't say which (e.g. "give me a trending topic for content", "make a
  video about X"): the user is then asked. Set `seconds` to the length they asked for
  (e.g. "1 minute" = 60), else null. Set `trending` true only when the
  user wants a video about whatever is trending/latest/hot now (a repo, paper, model or
  news item) rather than naming the exact thing.
- edit_tutorial: the user wants to change the tutorial already in this conversation
  (narration, wording, tone, hook, length, cutting or fast-forwarding a part, zoom,
  highlights, title, re-render).
- math_tutor: the user wants a math problem solved, explained or taught step by step,
  a formula derived or proved, an equation, derivative, integral or limit worked out,
  or a geometry construction or proof. It becomes a narrated board lesson (drawn, not
  a screen recording). Set `level` to "child" (primary school), "school" (high school)
  or "undergrad" when the message says or clearly implies who it's for, else null;
  `seconds` if they ask for a length.
- chat: anything else (questions, docs lookups, small talk).

Recipes:
{catalog}"""


async def route(ctx: JobContext, has_tutorial: bool) -> Route:
    text = ctx.input.get("text", "")
    names = [a.get("filename", "") for a in ctx.input.get("attachments") or []]
    files = f"Attached files: {', '.join(names)}\n" if names else ""
    if ctx.llm is not None and ctx.llm.configured:
        r, _ = await ctx.llm.parse(
            Route,
            purpose="assistant.route",
            model=ctx.settings.research_model,
            instructions=ROUTER.format(catalog=recipes.catalog()),
            input=f"Conversation already has a tutorial: {has_tutorial}\n{files}\nMessage: {text}",
        )
        if r.recipe not in recipes.recipes():
            r.recipe = None
        r.format = r.format or _format(text)
        r.seconds = r.seconds or _seconds(text)
        return r
    rid = recipes.match(text)
    wants_new = re.search(r"\b(tutorial|video|walkthrough|show me how)\b", text, re.I)
    if rid and (wants_new or not has_tutorial):
        return Route(intent="make_tutorial", recipe=rid)
    if has_tutorial:
        return Route(intent="edit_tutorial")
    if re.search(r"\btutorial\b", text, re.I):
        return Route(intent="make_tutorial", format="tutorial", seconds=_seconds(text))
    return Route(intent="chat")


def _format(text: str) -> Literal["tutorial", "reel", "explainer"] | None:
    for fmt, words in (
        ("explainer", r"explainer|explain(s|ing)? video|overview video"),
        ("reel", r"reels?|shorts?|tiktok|influencer"),
        ("tutorial", r"tutorials?|step[- ]by[- ]step|how[- ]to guide"),
    ):
        if re.search(rf"\b({words})\b", text, re.I):
            return fmt  # type: ignore[return-value]
    return None


def _seconds(text: str) -> int | None:
    m = re.search(r"\b(\d+)\s*(s|secs?|seconds?|m|mins?|minutes?)\b", text, re.I)
    if not m:
        return None
    return int(m[1]) * (60 if m[2].lower().startswith("m") else 1)


@job_handler("assistant.reply")
async def assistant_reply(ctx: JobContext) -> dict[str, Any] | None:
    conv = ctx.store.get_conversation(ctx.conversation_id) if ctx.conversation_id else None
    tuts = (
        [t for t in ctx.store.list_tutorials(conv["user_id"], conv["id"]) if t["capture"]]
        if conv
        else []
    )
    ctx.thinking("Router", "Reading your message")
    r = await route(ctx, bool(tuts))
    if r.intent == "make_tutorial":
        rid = None if r.trending else r.recipe
        mode, seconds = "tutorial", None
        if not rid and ctx.llm is not None and ctx.llm.configured:  # recipes are fixed scripts
            mode, seconds = await formats.clarify(ctx, r.format, r.seconds)
        return await pipeline.make(ctx, rid, goal=ctx.input.get("text"), mode=mode,
                                   trending=r.trending, seconds=seconds)  # fmt: skip
    if r.intent == "math_tutor" and ctx.llm is not None and ctx.llm.configured:
        return await tutor.make(ctx, ctx.input.get("text", ""), level=r.level, seconds=r.seconds)
    if r.intent == "edit_tutorial" and tuts:
        return await pipeline.edit(ctx, tuts[0]["id"], instruction=ctx.input.get("text"))
    if ctx.llm is not None and ctx.llm.configured:
        return await docs_research(ctx)
    return await demo_reply(ctx)
