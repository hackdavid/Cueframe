"""'Trending for you': today's hot repos, papers, models and tech news for creators, read on
demand from the trending agent's live sources (no scheduler), each with ready video prompts."""

import asyncio
import time
from typing import Any

import httpx

from app.agents.trending import Tools

NICHES = {
    "all": ("github", "papers", "models", "news"),
    "ai": ("papers", "models"),
    "dev": ("github", "news"),
}
TTL = 900
PER_SOURCE = 6
_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}


def _item(kind: str, title: str, blurb: str, url: str, metric: str) -> dict[str, Any]:
    about = f"{title} ({url})"
    return {
        "kind": kind,
        "title": title,
        "blurb": blurb[:180],
        "url": url,
        "metric": metric,
        "prompts": {
            "reel": f"Make a 45 second reel about {about}",
            "explainer": f"Make a 60 second explainer about {about}",
            "tutorial": f"Make a tutorial: get started with {about}",
        },
    }


async def _source(tools: Tools, name: str) -> list[dict[str, Any]]:
    if name == "github":
        return [
            _item("Repo", r["repo"], r["description"], r["url"],
                  f"★ {r['stars_gained']:,} {r['period']}".strip())
            for r in await tools.github_trending()
        ]  # fmt: skip
    if name == "papers":
        return [
            _item("Paper", p["title"], p["summary"], p["url"], f"▲ {p['upvotes']} upvotes")
            for p in await tools.hf_daily_papers(PER_SOURCE)
        ]
    if name == "models":
        return [
            _item("Model", m["id"], m["task"], m["url"], f"♥ {m['likes']:,} likes")
            for m in await tools.hf_trending("models", PER_SOURCE)
        ]
    return [
        _item("News", h["title"], f"{h['comments']} comments on Hacker News",
              h["url"] or h["discussion"], f"▲ {h['points']} points")
        for h in await tools.hacker_news(PER_SOURCE)
    ]  # fmt: skip


async def trending(niche: str = "all", tools: Tools | None = None) -> list[dict[str, Any]]:
    """Interleaved items from each source of the niche; a source that fails is skipped."""
    names = NICHES.get(niche, NICHES["all"])
    hit = _cache.get(niche)
    if hit and tools is None and time.monotonic() - hit[0] < TTL:
        return hit[1]

    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as http:
        t = tools or Tools(http)
        got = await asyncio.gather(*(_source(t, n) for n in names), return_exceptions=True)
    lists = [g[:PER_SOURCE] for g in got if isinstance(g, list)]
    longest = max((len(x) for x in lists), default=0)
    items = [lst[i] for i in range(longest) for lst in lists if i < len(lst)]
    if tools is None and items:
        _cache[niche] = (time.monotonic(), items)
    return items
