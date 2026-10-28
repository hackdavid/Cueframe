"""Docs research agent: question -> official docs -> best pages -> cleaned
text/image excerpts -> step-by-step answer with confidence. Falls back to the
model's own knowledge (clearly labelled) when the docs don't answer it.

Stages (each is one bounded model call; fetching/ranking costs no tokens):
  discover  web_search (low context, <=2 tool calls) -> official domains + URLs
  select    model picks the pages to read from ranked candidates
  read      fetch + clean pages locally, BM25-rank sections, attach doc images
  assess    model extracts steps from excerpts, or asks for up to 3 more links
  fallback  model answers from its own knowledge if confidence stays low
"""

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlparse

import httpx

from app.agents import web
from app.agents.contracts import (
    DocsDiscovery,
    KnowledgeSteps,
    PageCandidate,
    PageContent,
    PageSelection,
    ResearchRequest,
    ResearchResult,
    Source,
    Step,
    StepsDraft,
    TraceEntry,
)
from app.config import Settings
from app.llm import LLMResponse, MetaClient, Usage
from app.log import fields, get_logger

log = get_logger("agents.docs_research")
Progress = Callable[[str, str, float], Awaitable[None] | None]

DISCOVER = (
    "You locate official software documentation. Run one web search (two at most) aimed at "
    "the vendor's own docs/manual/help site. Return the app name, its official domains and up "
    "to 5 official documentation URLs most likely to answer the question. Prefer the vendor "
    "docs over forums, videos or third-party blogs."
)
SELECT = (
    "Pick the documentation pages most likely to contain the exact steps for the question. "
    "Only choose URLs from the candidate list. Prefer official how-to/manual pages."
)
ASSESS = (
    "You turn documentation excerpts into precise step-by-step instructions. Use only the "
    "excerpts and attached images. Each step must name exact menus, buttons and fields and "
    "cite the source URL it came from; set image_url only to an image URL listed in the "
    "excerpts. Set found=false and a low confidence if the excerpts do not clearly answer the "
    "question, describe what is missing, and choose up to 3 follow_links from the candidate "
    "links that are likely to contain it."
)
KNOWLEDGE = (
    "The official documentation did not answer this with high confidence. Answer from your own "
    "knowledge of the software. Give precise steps with exact menu/button names, mention "
    "version differences in caveats, and give an honest confidence. Do not invent URLs: leave "
    "source_url and image_url null."
)


class DocsResearchAgent:
    def __init__(self, llm: MetaClient, settings: Settings, http: httpx.AsyncClient | None = None):
        self.llm = llm
        self.settings = settings
        self.model = settings.research_model
        self._http = http

    async def run(
        self, req: ResearchRequest, on_progress: Progress | None = None
    ) -> ResearchResult:
        run = _Run(self, req, on_progress)
        if self._http is not None:
            return await run.execute(self._http)
        async with httpx.AsyncClient(timeout=20) as http:
            return await run.execute(http)


class _Run:
    def __init__(
        self, agent: DocsResearchAgent, req: ResearchRequest, on_progress: Progress | None
    ):
        self.a, self.req, self.on_progress = agent, req, on_progress
        self.max_pages = req.max_pages or agent.settings.research_max_pages
        self.threshold = req.confidence_threshold or agent.settings.research_confidence
        self.trace: list[TraceEntry] = []
        self.usage = Usage()
        self.pages: dict[str, PageContent] = {}
        self.image_urls: set[str] = set()

    async def progress(self, stage: str, label: str, value: float) -> None:
        log.info(label, extra=fields(stage=stage))
        if self.on_progress:
            out = self.on_progress(stage, label, value)
            if inspect.isawaitable(out):
                await out

    def record(self, stage: str, detail: str, res: LLMResponse | None = None) -> None:
        if res:
            self.usage = self.usage + res.usage
        self.trace.append(
            TraceEntry(
                stage=stage,
                detail=detail,
                model=res.model if res else None,
                usage=res.usage if res else None,
                latency_s=res.latency_s if res else 0.0,
            )
        )

    async def call(self, schema: Any, stage: str, effort: str, **kw: Any) -> Any:
        parsed, res = await self.a.llm.parse(
            schema, purpose=f"docs.{stage}", model=self.a.model, reasoning_effort=effort, **kw
        )
        return parsed, res

    async def execute(self, http: httpx.AsyncClient) -> ResearchResult:
        q = self.req.query
        await self.progress("discover", "Searching for the official docs", 0.05)
        disc, res = await self.call(
            DocsDiscovery,
            "discover",
            "low",
            instructions=DISCOVER,
            input=f"App: {self.req.app or 'infer it from the question'}\nQuestion: {q}",
            tools=[{"type": "web_search", "search_context_size": "low"}],
            max_tool_calls=2,
            include=["web_search_call.results"],
        )
        cands = self.candidates(disc, res)
        self.record("discover", f"{disc.app}: {len(cands)} candidate pages", res)
        app = self.req.app or disc.app

        draft: StepsDraft | None = None
        to_read = await self.select(cands)
        for rnd in range(1, self.a.settings.research_max_rounds + 1):
            await self.progress("read", f"Reading {len(to_read)} doc pages", 0.2 + 0.3 * (rnd - 1))
            await self.read(http, to_read)
            if not self.pages:
                break
            await self.progress("assess", "Extracting the steps from the docs", 0.4 + 0.3 * rnd)
            draft, links = await self.assess(http, rnd)
            if draft.found and draft.steps and draft.confidence >= self.threshold:
                return self.result(app, "docs", draft.confidence, draft.steps, "")
            to_read = [u for u in draft.follow_links if u in links and u not in self.pages][:3]
            if not to_read:
                break

        await self.progress("fallback", "Docs were not conclusive; using model knowledge", 0.85)
        missing = draft.missing if draft else "no documentation page could be read"
        kn, res = await self.call(
            KnowledgeSteps,
            "fallback",
            "medium",
            instructions=KNOWLEDGE,
            input=f"App: {app}\nQuestion: {q}\nWhat the docs were missing: {missing}",
        )
        self.record("fallback", f"model knowledge, confidence {kn.confidence:.2f}", res)
        for s in kn.steps:
            s.source_url = s.image_url = None
        return self.result(app, "model_knowledge", kn.confidence, kn.steps, kn.caveats)

    def candidates(self, disc: DocsDiscovery, res: LLMResponse) -> list[PageCandidate]:
        found: dict[str, PageCandidate] = {}
        for url in disc.urls:
            found.setdefault(url, PageCandidate(url=url, origin="search"))
        for call in res.search_calls():
            for r in call.get("results") or []:
                if r.get("url"):
                    found.setdefault(
                        r["url"],
                        PageCandidate(
                            url=r["url"],
                            title=str(r.get("title") or "")[:200],
                            snippet=str(r.get("snippet") or r.get("text") or "")[:300],
                        ),
                    )
        domains = [d.lower().removeprefix("www.") for d in disc.official_domains]

        def official(url: str) -> bool:
            host = (urlparse(url).hostname or "").lower()
            return any(host == d or host.endswith("." + d) for d in domains)

        cands = list(found.values())
        return [c for c in cands if official(c.url)] or cands

    async def select(self, cands: list[PageCandidate]) -> list[str]:
        ranked = web.rank_candidates(self.req.query, cands)[:20]
        if len(ranked) <= self.max_pages:
            return [c.url for c in ranked]
        listing = "\n".join(f"- {c.url} | {c.title} | {c.snippet}" for c in ranked)
        sel, res = await self.call(
            PageSelection,
            "select",
            "low",
            instructions=SELECT,
            input=f"Question: {self.req.query}\nPick up to {self.max_pages}.\n"
            f"Candidates:\n{listing}",
        )
        allowed = {c.url for c in ranked}
        urls = [u for u in sel.urls if u in allowed][: self.max_pages]
        urls = urls or [c.url for c in ranked[: self.max_pages]]
        self.record("select", f"chose {len(urls)}: {sel.reason[:160]}", res)
        return urls

    async def read(self, http: httpx.AsyncClient, urls: list[str]) -> None:
        results = await asyncio.gather(
            *(web.fetch_page(http, u) for u in urls), return_exceptions=True
        )
        ok = []
        for url, page in zip(urls, results, strict=True):
            if isinstance(page, BaseException):
                log.warning("page fetch failed", extra=fields(url=url, error=str(page)[:200]))
                continue
            self.pages[url] = page
            ok.append(url)
        self.record("read", f"fetched {len(ok)}/{len(urls)}: {', '.join(ok)}")

    async def assess(self, http: httpx.AsyncClient, rnd: int) -> tuple[StepsDraft, set[str]]:
        q = self.req.query
        picked = web.top_sections(q, list(self.pages.values()))
        blocks, images = [], []
        for i, (page, sec) in enumerate(picked, 1):
            imgs = "".join(f"\n  image: {im.url} ({im.alt})" for im in sec.images[:3])
            self.image_urls.update(im.url for im in sec.images[:3])
            images += sec.images[:1]
            blocks.append(
                f"### [{i}] {page.title} > {sec.heading}\nURL: {page.url}\n{sec.text}{imgs}"
            )
        links = {c.url: c for p in self.pages.values() for c in p.links if c.url not in self.pages}
        ranked_links = web.rank_candidates(q, list(links.values()))[:25]
        content: list[dict[str, Any]] = [
            {
                "type": "input_text",
                "text": f"Question: {q}\n\nDocumentation excerpts:\n\n"
                + "\n\n".join(blocks)
                + "\n\nCandidate links:\n"
                + "\n".join(f"- {c.url} | {c.title}" for c in ranked_links),
            }
        ]
        sent = 0
        if self.req.include_images:
            for im in images[:4]:
                data = await web.fetch_image_data_url(http, im.url)
                if data and sent < 2:
                    content += [
                        {"type": "input_text", "text": f"Image {im.url} ({im.alt}):"},
                        {"type": "input_image", "image_url": data},
                    ]
                    sent += 1
        draft, res = await self.call(
            StepsDraft,
            "assess",
            "medium",
            instructions=ASSESS,
            input=[{"role": "user", "content": content}],
        )
        for s in draft.steps:
            if s.source_url not in self.pages:
                s.source_url = None
            if s.image_url not in self.image_urls:
                s.image_url = None
        self.record(
            "assess",
            f"round {rnd}: {len(picked)} sections, {sent} images -> found={draft.found} "
            f"confidence={draft.confidence:.2f}"
            + (f", missing: {draft.missing[:160]}" if not draft.found else ""),
            res,
        )
        return draft, {c.url for c in ranked_links}

    def result(
        self, app: str | None, source: str, conf: float, steps: list[Step], caveats: str
    ) -> ResearchResult:
        for i, s in enumerate(steps, 1):
            s.index = i
        log.info(
            "research done",
            extra=fields(
                source=source, confidence=conf, steps=len(steps), **self.usage.model_dump()
            ),
        )
        return ResearchResult(
            query=self.req.query,
            app=app,
            source=source,  # type: ignore[arg-type]
            confidence=conf,
            steps=steps,
            caveats=caveats,
            sources=[Source(url=u, title=p.title) for u, p in self.pages.items()],
            pages_read=list(self.pages),
            trace=self.trace,
            usage=self.usage,
        )


def render_markdown(r: ResearchResult) -> str:
    origin = (
        "from the official docs"
        if r.source == "docs"
        else "**from the model's own knowledge** (the docs did not answer it with high confidence)"
    )
    lines = [
        f"**{r.app or 'Steps'}:** {r.query}",
        "",
        f"Steps {origin}, confidence {r.confidence:.0%}.",
        "",
    ]
    for s in r.steps:
        path = f" (`{s.ui_path}`)" if s.ui_path else ""
        cite = f" [source]({s.source_url})" if s.source_url else ""
        lines.append(f"{s.index}. **{s.title}**{path}: {s.detail}{cite}")
        if s.image_url:
            lines.append(f"\n   ![{s.title}]({s.image_url})\n")
    if r.caveats:
        lines += ["", f"_Caveats:_ {r.caveats}"]
    if r.sources:
        lines += ["", "**Docs read:**", *(f"- [{s.title}]({s.url})" for s in r.sources)]
    u = r.usage
    lines += [
        "",
        f"<sub>{u.input_tokens:,} input / {u.output_tokens:,} output tokens, "
        f"{u.web_searches} searches, about ${u.cost_usd:.4f}</sub>",
    ]
    return "\n".join(lines)
