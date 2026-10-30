"""Trending agent: finds what's hot today (GitHub repos, papers, models, tech news) with
live source tools, reads the chosen item's own README or abstract, and writes a `TrendBrief`
whose facts all come from those sources. The tutorial planner turns the brief into a video.

The model drives the tools through function calling; the tools only read public pages and
APIs (no keys needed): GitHub Trending, Hugging Face (daily papers, trending models and
Spaces), Hacker News (Algolia), arXiv."""

import asyncio
import html
import json
import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, Literal

import httpx
from pydantic import BaseModel, Field

from app.agents.web import UA
from app.llm import MetaClient

MAX_ROUNDS = 6
MAX_TOOL_TEXT = 6000
README_CHARS = 5000


def _strip(text: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", text))).strip()


def _num(text: str) -> int:
    return int(re.sub(r"\D", "", text) or 0)


def parse_github_trending(page: str) -> list[dict[str, Any]]:
    out = []
    for a in re.findall(r'<article class="Box-row">(.*?)</article>', page, re.S):
        name = re.search(r'<h2[^>]*>\s*<a[^>]*href="/([^"/]+/[^"/]+)"', a, re.S)
        if not name:
            continue
        desc = re.search(r'<p class="col-9[^"]*">(.*?)</p>', a, re.S)
        lang = re.search(r'itemprop="programmingLanguage">([^<]+)<', a)
        stars = re.search(r'href="/[^"]+/stargazers"[^>]*>(.*?)</a>', a, re.S)
        today = re.search(r"([\d,]+) stars? (today|this week|this month)", a)
        out.append({
            "repo": name.group(1),
            "url": f"https://github.com/{name.group(1)}",
            "description": _strip(desc.group(1)) if desc else "",
            "language": lang.group(1).strip() if lang else "",
            "stars": _num(_strip(stars.group(1))) if stars else 0,
            "stars_gained": _num(today.group(1)) if today else 0,
            "period": today.group(2) if today else "",
        })  # fmt: skip
    return out


class Tools:
    """The agent's source tools. Each returns JSON-able data; errors come back as text so
    the model can pick another source."""

    def __init__(self, http: httpx.AsyncClient):
        self.http = http

    async def _get(self, url: str, **params: Any) -> httpx.Response:
        r = await self.http.get(url, params=params or None, headers={"User-Agent": UA})
        r.raise_for_status()
        return r

    async def github_trending(self, since: str = "daily", language: str = "") -> Any:
        path = f"https://github.com/trending/{language}".rstrip("/")
        repos = parse_github_trending((await self._get(path, since=since)).text)
        return [{"rank": i, **r} for i, r in enumerate(repos[:15], 1)]

    async def hf_daily_papers(self, limit: int = 15) -> Any:
        data = (await self._get("https://huggingface.co/api/daily_papers", limit=50)).json()
        papers = []
        for d in data:
            p = d.get("paper", {})
            papers.append({
                "arxiv_id": p.get("id"), "title": p.get("title", "").strip(),
                "url": f"https://huggingface.co/papers/{p.get('id')}",
                "upvotes": p.get("upvotes", 0), "comments": d.get("numComments", 0),
                "summary": (p.get("ai_summary") or p.get("summary", ""))[:400],
                "github_repo": p.get("githubRepo") or "", "github_stars": p.get("githubStars"),
                "project_page": p.get("projectPage") or "", "published": d.get("publishedAt"),
            })  # fmt: skip
        papers.sort(key=lambda x: x["upvotes"], reverse=True)
        return [{"upvote_rank": i, **p} for i, p in enumerate(papers[: max(1, min(limit, 30))], 1)]

    async def hf_trending(self, kind: str = "models", limit: int = 10) -> Any:
        kind = kind if kind in ("models", "spaces") else "models"
        data = (
            await self._get(f"https://huggingface.co/api/{kind}", sort="trendingScore",
                            limit=max(1, min(limit, 20)))
        ).json()  # fmt: skip
        return [
            {"id": d.get("id"),
             "url": f"https://huggingface.co/{'spaces/' if kind == 'spaces' else ''}{d.get('id')}",
             "likes": d.get("likes"), "trending_score": d.get("trendingScore"),
             "task": d.get("pipeline_tag") or d.get("sdk") or ""}
            for d in data
        ]  # fmt: skip

    async def hacker_news(self, limit: int = 15) -> Any:
        data = (
            await self._get("https://hn.algolia.com/api/v1/search", tags="front_page",
                            hitsPerPage=max(1, min(limit, 30)))
        ).json()  # fmt: skip
        return [
            {"title": h.get("title"), "url": h.get("url") or "", "points": h.get("points"),
             "comments": h.get("num_comments"),
             "discussion": f"https://news.ycombinator.com/item?id={h.get('objectID')}"}
            for h in data.get("hits", [])
        ]  # fmt: skip

    async def arxiv_latest(self, category: str = "cs.AI", limit: int = 10) -> Any:
        if not re.fullmatch(r"[a-z-]+(\.[A-Za-z-]+)?", category):
            return "category must look like cs.AI or cs.CL"
        xml = (
            await self._get("https://export.arxiv.org/api/query", search_query=f"cat:{category}",
                            sortBy="submittedDate", max_results=max(1, min(limit, 20)))
        ).text  # fmt: skip
        out = []
        for e in re.findall(r"<entry>(.*?)</entry>", xml, re.S):
            ident = re.search(r"<id>https?://arxiv.org/abs/([^<]+)</id>", e)
            title = re.search(r"<title>(.*?)</title>", e, re.S)
            summ = re.search(r"<summary>(.*?)</summary>", e, re.S)
            out.append({"arxiv_id": ident.group(1) if ident else "",
                        "title": _strip(title.group(1)) if title else "",
                        "summary": _strip(summ.group(1))[:400] if summ else ""})  # fmt: skip
        return out

    async def read_github_repo(self, repo: str) -> Any:
        if not re.fullmatch(r"\w[\w.-]*/\w[\w.-]*", repo):
            return "repo must be owner/name"
        page = (await self._get(f"https://github.com/{repo}")).text
        about = re.search(r'<meta name="description" content="([^"]*)"', page)
        for name in ("README.md", "readme.md", "README.rst", "README"):
            r = await self.http.get(f"https://raw.githubusercontent.com/{repo}/HEAD/{name}",
                                    headers={"User-Agent": UA})  # fmt: skip
            if r.status_code == 200:
                readme = r.text
                break
        else:
            readme = ""
        heads = re.findall(r"^#{1,3}\s+(.+)$", readme, re.M)
        return {"repo": repo, "url": f"https://github.com/{repo}",
                "about": html.unescape(about.group(1)) if about else "",
                "readme_headings": [_strip(h) for h in heads][:25],
                "readme": readme[:README_CHARS]}  # fmt: skip

    async def read_paper(self, arxiv_id: str) -> Any:
        if not re.fullmatch(r"\d{4}\.\d{4,5}(v\d+)?", arxiv_id):
            return "arxiv_id must look like 2510.12345"
        r = await self.http.get(f"https://huggingface.co/api/papers/{arxiv_id}",
                                headers={"User-Agent": UA})  # fmt: skip
        if r.status_code == 200:
            p = r.json()
            return {"arxiv_id": arxiv_id, "title": p.get("title"),
                    "hf_page": f"https://huggingface.co/papers/{arxiv_id}",
                    "arxiv_page": f"https://arxiv.org/abs/{arxiv_id}",
                    "abstract": p.get("summary", ""), "upvotes": p.get("upvotes"),
                    "authors": [a.get("name") for a in p.get("authors", [])][:8],
                    "github_repo": p.get("githubRepo") or "",
                    "project_page": p.get("projectPage") or ""}  # fmt: skip
        items = await self.arxiv_latest_by_id(arxiv_id)
        return items or f"paper {arxiv_id} not found"

    async def read_model(self, model_id: str) -> Any:
        if not re.fullmatch(r"[\w.-]+/[\w.-]+", model_id):
            return "model_id must be owner/name"
        r = await self.http.get(f"https://huggingface.co/api/models/{model_id}",
                                headers={"User-Agent": UA})  # fmt: skip
        if r.status_code != 200:
            return f"model {model_id} not found on Hugging Face"
        d = r.json()
        card = await self.http.get(f"https://huggingface.co/{model_id}/raw/main/README.md",
                                   headers={"User-Agent": UA})  # fmt: skip
        readme = card.text if card.status_code == 200 else ""
        readme = re.sub(r"\A---\n.*?\n---\n", "", readme, flags=re.S)
        return {"model_id": model_id, "url": f"https://huggingface.co/{model_id}",
                "likes": d.get("likes"), "downloads": d.get("downloads"),
                "pipeline_tag": d.get("pipeline_tag") or "",
                "readme_headings": [_strip(h) for h in
                                    re.findall(r"^#{1,3}\s+(.+)$", readme, re.M)][:25],
                "readme": readme[:README_CHARS]}  # fmt: skip

    async def arxiv_latest_by_id(self, arxiv_id: str) -> Any:
        xml = (await self._get("https://export.arxiv.org/api/query", id_list=arxiv_id)).text
        summ = re.search(r"<entry>.*?<title>(.*?)</title>.*?<summary>(.*?)</summary>", xml, re.S)
        if not summ:
            return None
        return {"arxiv_id": arxiv_id, "title": _strip(summ.group(1)),
                "arxiv_page": f"https://arxiv.org/abs/{arxiv_id}",
                "abstract": _strip(summ.group(2))}  # fmt: skip


def _fn(name: str, description: str, props: dict[str, Any]) -> dict[str, Any]:
    return {"type": "function", "name": name, "description": description, "strict": True,
            "parameters": {"type": "object", "properties": props, "required": list(props),
                           "additionalProperties": False}}  # fmt: skip


INT = {"type": "integer"}
TOOL_SPECS = [
    _fn("github_trending", "GitHub Trending: repos gaining the most stars now, with stars gained",
        {"since": {"type": "string", "enum": ["daily", "weekly", "monthly"]},
         "language": {"type": "string", "description": "e.g. 'python', or '' for all"}}),
    _fn("hf_daily_papers", "Hugging Face Daily Papers: today's papers ranked by upvotes, with "
        "linked GitHub repo", {"limit": INT}),
    _fn("hf_trending", "Hugging Face trending models or Spaces (runnable demos)",
        {"kind": {"type": "string", "enum": ["models", "spaces"]}, "limit": INT}),
    _fn("hacker_news", "Hacker News front page: tech news and launches with points",
        {"limit": INT}),
    _fn("arxiv_latest", "Newest arXiv submissions in a category (not ranked by popularity)",
        {"category": {"type": "string"}, "limit": INT}),
    _fn("read_github_repo", "Read a GitHub repo's About text, README headings and README",
        {"repo": {"type": "string", "description": "owner/name"}}),
    _fn("read_model", "Read a Hugging Face model's card (README), likes and downloads",
        {"model_id": {"type": "string", "description": "owner/name on Hugging Face"}}),
    _fn("read_paper", "Read a paper's abstract, authors, upvotes and linked code",
        {"arxiv_id": {"type": "string"}}),
]  # fmt: skip


class TrendBrief(BaseModel):
    """LLM contract: one trending item, with only facts the tools returned."""

    found: bool = Field(description="False if no tool gave a usable item")
    kind: Literal["repo", "paper", "model", "news"]
    name: str = Field(description="e.g. 'HKUDS/DeepTutor' or the paper title")
    source: str = Field(description="Where it is trending, e.g. 'GitHub Trending'")
    why_trending: str = Field(
        description="The tool's rank and numbers as read today, with today's date (counts "
        "are as of today, not the publish date), e.g. '#1 on GitHub Trending, 2,376 stars "
        "today (2026-10-26)'"
    )
    url: str = Field(description="The page to open in the video (repo page or paper page)")
    extra_urls: list[str] = Field(
        description="Up to 2 related public pages from the tools: paper code repo, project "
        "page, demo Space; [] if none"
    )
    one_liner: str = Field(description="What it is, in one plain sentence")
    facts: list[str] = Field(
        description="4-8 short facts taken word-for-word or closely from the README/abstract "
        "(what it does, how, results, how to try it); nothing from memory"
    )
    show: list[str] = Field(
        description="Visible headings or words on its page worth scrolling to in the video "
        "(README headings, 'Abstract'), exactly as the tool returned them"
    )
    reason: str = Field(description="Why this item was picked, or why none was found")


INSTRUCTIONS = """You are Cueframe's trending scout. Today is {today}. Use the tools to find
the single best item for the request: something genuinely trending right now (by the tools'
own numbers) that makes a good 60-second explainer video in a web browser. Look at a
ranking tool first, pick one item (prefer ones with a clear README or abstract and, for
papers, linked code or a project page), then read it with read_github_repo, read_paper
or, for a Hugging Face model, read_model (a model id is not a GitHub repo).
Skip sponsored entries, items whose page isn't in English (the video is narrated in
English), and tools for piracy or for getting around a service's limits or terms.
Never invent facts or numbers: every fact must come from what the tools returned. When you
are done, answer with a short note of your pick."""


ToolFn = Callable[..., Awaitable[Any]]


class TrendingAgent:
    def __init__(self, llm: MetaClient, model: str, http: httpx.AsyncClient | None = None):
        self.llm, self.model, self._http = llm, model, http

    async def run(
        self, request: str, progress: Callable[[str], None] = lambda _m: None
    ) -> tuple[TrendBrief, list[dict[str, Any]]]:
        """The brief, plus a trace of every tool call (name, args, short result)."""
        if self._http is not None:
            return await self._run(self._http, request, progress)
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as http:
            return await self._run(http, request, progress)

    async def _run(
        self, http: httpx.AsyncClient, request: str, progress: Callable[[str], None]
    ) -> tuple[TrendBrief, list[dict[str, Any]]]:
        tools = Tools(http)
        fns: dict[str, ToolFn] = {s["name"]: getattr(tools, s["name"]) for s in TOOL_SPECS}
        today = datetime.now(UTC).date().isoformat()
        instructions = INSTRUCTIONS.format(today=today)
        convo: list[dict[str, Any]] = [{"role": "user", "content": request}]
        trace: list[dict[str, Any]] = []
        for _ in range(MAX_ROUNDS):
            res = await self.llm.respond(
                purpose="trending.scout", model=self.model, input=convo, tools=TOOL_SPECS,
                instructions=instructions, reasoning_effort="low",
            )  # fmt: skip
            calls = [o for o in res.output if o.get("type") == "function_call"]
            if not calls:
                break
            convo += res.output

            async def call(c: dict[str, Any]) -> dict[str, Any]:
                name = c.get("name", "")
                try:
                    args = json.loads(c.get("arguments") or "{}")
                    progress(f"{name}({', '.join(f'{v}' for v in args.values())})")
                    out = await fns[name](**args)
                    text = json.dumps(out, ensure_ascii=False, default=str)
                except Exception as exc:  # a failing source is reported, not fatal
                    args, text = {}, f"error: {type(exc).__name__}: {exc}"
                trace.append({"tool": name, "args": args, "result": text[:300]})
                return {"type": "function_call_output", "call_id": c.get("call_id"),
                        "output": text[:MAX_TOOL_TEXT]}  # fmt: skip

            convo += await asyncio.gather(*(call(c) for c in calls))
        brief, _ = await self.llm.parse(
            TrendBrief, purpose="trending.brief", model=self.model, instructions=instructions,
            input=convo + [{"role": "user", "content": "Write the brief for your pick now, "
                            "using only what the tools returned."}],
            reasoning_effort="low",
        )  # fmt: skip
        return brief, trace


def brief_text(b: TrendBrief) -> str:
    """The brief as grounding for the planner."""
    facts = "\n".join(f"- {f}" for f in b.facts)
    return (
        f"Trending item ({b.source}): {b.name} [{b.kind}]\nWhy it's trending: {b.why_trending}\n"
        f"Page to open: {b.url}\nRelated pages: {', '.join(b.extra_urls) or 'none'}\n"
        f"What it is: {b.one_liner}\nFacts from its own page (use only these):\n{facts}\n"
        f"Visible headings worth scrolling to: {'; '.join(b.show) or 'none'}"
    )
