"""Live run of the docs research agent against Meta (needs MODEL_API_KEY).

uv run python scripts/docs_research_live.py "How do I export a vertical video?" Shotcut
"""

import asyncio
import json
import sys

from app.agents.contracts import ResearchRequest
from app.agents.docs_research import DocsResearchAgent, render_markdown
from app.config import get_settings
from app.llm import MetaClient
from app.log import setup_logging


async def main() -> None:
    setup_logging()
    settings = get_settings()
    llm = MetaClient.from_settings(settings)
    query, app = sys.argv[1], (sys.argv[2] if len(sys.argv) > 2 else None)
    result = await DocsResearchAgent(llm, settings).run(ResearchRequest(query=query, app=app))
    await llm.aclose()
    print(render_markdown(result))
    print(json.dumps([t.model_dump() for t in result.trace], indent=1))


asyncio.run(main())
