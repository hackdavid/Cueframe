from typing import Any

from app.agents.contracts import ResearchRequest
from app.agents.docs_research import DocsResearchAgent, render_markdown
from app.jobs import JobContext, job_handler


@job_handler("docs.research")
async def docs_research(ctx: JobContext) -> dict[str, Any]:
    if ctx.llm is None or not ctx.llm.configured:
        raise RuntimeError("MODEL_API_KEY is not configured")
    req = ResearchRequest(query=ctx.input.get("text", ""), app=ctx.input.get("app"))
    if ctx.input.get("context"):  # conversation brief from the assistant router
        req.query = f"{ctx.input['context']}\nLatest message: {req.query}"

    def progress(stage: str, label: str, value: float) -> None:
        ctx.phase(stage, label)
        ctx.progress(value, label)

    result = await DocsResearchAgent(ctx.llm, ctx.settings).run(req, progress)
    if ctx.conversation_id:
        ctx.reply(render_markdown(result))
    return result.model_dump(mode="json")
