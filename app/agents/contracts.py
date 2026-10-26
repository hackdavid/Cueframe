"""Pydantic contracts for the docs research agent. Models marked "LLM contract" are
sent to Meta as JSON schemas and the model output is validated against them."""

from typing import Literal

from pydantic import BaseModel, Field

from app.llm import Usage


class ResearchRequest(BaseModel):
    query: str = Field(min_length=3, max_length=2000, description="What the user wants to do")
    app: str | None = Field(None, description="Software name, if known (e.g. 'Shotcut')")
    max_pages: int | None = Field(None, ge=1, le=8, description="Pages to read per round")
    include_images: bool = Field(True, description="Send doc screenshots to the model")
    confidence_threshold: float | None = Field(None, ge=0, le=1)


class DocsDiscovery(BaseModel):
    """LLM contract: where the official documentation lives."""

    app: str = Field(description="Software name")
    official_domains: list[str] = Field(description="Domains of the official docs/site")
    urls: list[str] = Field(description="Most relevant official documentation URLs, best first")


class PageSelection(BaseModel):
    """LLM contract: which candidate pages to read."""

    urls: list[str]
    reason: str


class DocImage(BaseModel):
    url: str
    alt: str = ""


class Section(BaseModel):
    heading: str
    text: str = ""
    images: list[DocImage] = []


class PageCandidate(BaseModel):
    url: str
    title: str = ""
    snippet: str = ""
    origin: Literal["search", "link"] = "search"


class PageContent(BaseModel):
    url: str
    title: str
    sections: list[Section]
    links: list[PageCandidate]


class Step(BaseModel):
    index: int = Field(ge=1)
    title: str = Field(description="Short imperative step title")
    detail: str = Field(description="What to do, with exact menu/button names")
    ui_path: str | None = Field(None, description="Menu path, e.g. 'File > Export'")
    source_url: str | None = Field(None, description="Doc URL this step comes from")
    image_url: str | None = Field(None, description="Doc image URL that illustrates the step")


class StepsDraft(BaseModel):
    """LLM contract: steps extracted from the doc excerpts."""

    found: bool = Field(description="True if the excerpts answer the question")
    confidence: float = Field(ge=0, le=1, description="Confidence the steps are correct")
    steps: list[Step]
    missing: str = Field(description="What information is missing, empty if none")
    follow_links: list[str] = Field(description="Candidate links worth reading next (max 3)")


class KnowledgeSteps(BaseModel):
    """LLM contract: steps from the model's own knowledge."""

    confidence: float = Field(ge=0, le=1)
    steps: list[Step]
    caveats: str = Field(description="Version differences or uncertainties")


class Source(BaseModel):
    url: str
    title: str


class TraceEntry(BaseModel):
    stage: str
    detail: str
    model: str | None = None
    usage: Usage | None = None
    latency_s: float = 0.0


class ResearchResult(BaseModel):
    query: str
    app: str | None
    source: Literal["docs", "model_knowledge"]
    confidence: float
    steps: list[Step]
    caveats: str = ""
    sources: list[Source]
    pages_read: list[str]
    trace: list[TraceEntry]
    usage: Usage
