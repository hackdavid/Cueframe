"""Tutorial jobs. `assistant.reply` is the default chat job: it reads the message and
either makes a tutorial, edits the conversation's tutorial, or answers normally."""

import re
from typing import Any, Literal

from pydantic import BaseModel

from app.handlers.demo import demo_reply
from app.handlers.research import docs_research
from app.jobs import JobContext, JobManager, job_handler
from app.tutorial import pipeline, recipes


@job_handler("tutorial.make")
async def tutorial_make(ctx: JobContext) -> dict[str, Any]:
    ji = ctx.input.get("job_input") or {}
    rid = ji.get("recipe") or recipes.match(ctx.input["text"])
    mode = "influencer" if ji.get("mode") == "influencer" else "tutorial"
    return await pipeline.make(ctx, rid, goal=ctx.input["text"], mode=mode,
                               trending=bool(ji.get("trending")))  # fmt: skip


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
    intent: Literal["make_tutorial", "edit_tutorial", "chat"]
    recipe: str | None = None
    mode: Literal["tutorial", "influencer"] = "tutorial"
    trending: bool = False


ROUTER = """You route messages in Cueframe, an app that makes narrated video tutorials
from real runs in a Linux desktop sandbox. Pick:
- make_tutorial: the user wants a (new) tutorial/video/walkthrough of a task. Set
  `recipe` to the id of the matching recipe below, or null if none fits (a new
  tutorial is then planned automatically). Set `mode`: "influencer" when the user wants
  an engaging influencer/creator/social-style explainer (e.g. about a trending repo,
  paper or launch), else "tutorial" (a how-to lesson). Set `trending` true only when the
  user wants a video about whatever is trending/latest/hot now (a repo, paper, model or
  news item) rather than naming the exact thing.
- edit_tutorial: the user wants to change the tutorial already in this conversation
  (narration, wording, zoom, highlights, title, re-render).
- chat: anything else (questions, docs lookups, small talk).

Recipes:
{catalog}"""


async def route(ctx: JobContext, has_tutorial: bool) -> Route:
    text = ctx.input.get("text", "")
    if ctx.llm is not None and ctx.llm.configured:
        r, _ = await ctx.llm.parse(
            Route,
            purpose="assistant.route",
            model=ctx.settings.research_model,
            instructions=ROUTER.format(catalog=recipes.catalog()),
            input=f"Conversation already has a tutorial: {has_tutorial}\n\nMessage: {text}",
        )
        if r.recipe not in recipes.recipes():
            r.recipe = None
        return r
    rid = recipes.match(text)
    wants_new = re.search(r"\b(tutorial|video|walkthrough|show me how)\b", text, re.I)
    if rid and (wants_new or not has_tutorial):
        return Route(intent="make_tutorial", recipe=rid)
    if has_tutorial:
        return Route(intent="edit_tutorial")
    if re.search(r"\btutorial\b", text, re.I):
        return Route(intent="make_tutorial")
    return Route(intent="chat")


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
        return await pipeline.make(ctx, None if r.trending else r.recipe,
                                   goal=ctx.input.get("text"), mode=r.mode,
                                   trending=r.trending)  # fmt: skip
    if r.intent == "edit_tutorial" and tuts:
        return await pipeline.edit(ctx, tuts[0]["id"], instruction=ctx.input.get("text"))
    if ctx.llm is not None and ctx.llm.configured:
        return await docs_research(ctx)
    return await demo_reply(ctx)
