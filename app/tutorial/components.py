"""Motion overlay components: the registry the planner picks from. Each component is
one entry here (a pydantic props schema, a validator bound to the job's facts, a doc
line for the planner prompt) plus one React file in renderer/src/components/. Adding a
component never touches the planner, the timeline or the renderer's scene code.

Props reach the video resolved (`resolve` turns fact_key into the fact's value/number/
series), so a number the facts don't carry can never be rendered."""

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field

from app.agents import UA
from app.tutorial.contracts import OverlaySpec
from app.tutorial.facts import Facts, validate_numbers


@dataclass(frozen=True)
class Context:
    """What a validator may check against: the job's verified facts and source brief."""

    facts: Facts
    brief: str = ""


Validator = Callable[[dict[str, Any], Context], list[str]]


@dataclass(frozen=True)
class Component:
    key: str
    schema: type[BaseModel]
    doc: str  # 1-2 lines for the planner prompt: what it shows, its props, its length
    default_seconds: float
    max_per_state: int = 1
    validator: Validator | None = None


REGISTRY: dict[str, Component] = {}


def register(key: str, schema: type[BaseModel], doc: str, default_seconds: float,
             max_per_state: int = 1) -> Callable[[Validator], Validator]:  # fmt: skip
    def deco(fn: Validator) -> Validator:
        REGISTRY[key] = Component(key, schema, doc, default_seconds, max_per_state, fn)
        return fn

    return deco


def prompt_doc() -> str:
    """The registry as planner instructions; stable order so prompts don't drift."""
    return "\n".join(f"- {c.key}: {c.doc}" for c in REGISTRY.values())


class _Props(BaseModel):
    model_config = ConfigDict(extra="forbid")  # a hallucinated prop name is a plan error


def validate_overlay(o: OverlaySpec, ctx: Context) -> list[str]:
    """Problems-style strings for one overlay; [] when it can be rendered truthfully."""
    c = REGISTRY.get(o.component)
    if c is None:
        return [f"overlay {o.id}: unknown component {o.component!r}"]
    try:
        parsed = c.schema.model_validate(o.props)
    except Exception as exc:
        return [f"overlay {o.id} ({o.component}): {str(exc).splitlines()[0]}"]
    props = parsed.model_dump()  # schema defaults applied before validating
    out = list(c.validator(props, ctx) if c.validator else [])
    return out + validate_numbers(o.component, props, ctx.facts)


def resolve(component: str, props: dict[str, Any], facts: Facts) -> dict[str, Any]:
    """Inline the referenced fact's display value, number and series into the props
    (schema defaults applied, so an omitted fact_key still resolves)."""
    c = REGISTRY.get(component)
    out = c.schema.model_validate(props).model_dump() if c else dict(props)
    if key := out.get("fact_key"):
        f = facts.items.get(key)
        if f is None:
            raise ValueError(f"no fact {key!r}")
        out.update(value=f.value, fact_label=f.label, as_of=f.as_of)
        if f.number is not None:
            out["number"] = f.number
        if f.values is not None:
            out["values"] = f.values
    return out


# --- the initial components -------------------------------------------------


class StatChipProps(_Props):
    fact_key: str = Field(description="A key from FACTS, e.g. 'github.stars'")
    label: str = Field("", description="Short caption under the number; defaults to the "
                           "fact's own label")  # fmt: skip
    prefix: str = ""
    suffix: str = ""


class StarCounterProps(_Props):
    fact_key: str = Field("github.stars", description="A FACTS key with a number")
    sub: str = Field("", description="Small line under the counter, e.g. 'and growing'")


class ContribGraphProps(_Props):
    fact_key: str = Field("github.commit_activity",
                          description="A FACTS key carrying a weekly series")  # fmt: skip


class LogoWallProps(_Props):
    orgs: list[str] = Field(description="GitHub org or user logins using the project, "
                            "2-12, exactly as on github.com")  # fmt: skip


class ClaimCardProps(_Props):
    quote: str = Field(description="A sentence copied exactly from the source")
    source_ref: str = Field("", description="Where it appears, e.g. 'README' or 'page 3'")


def _fact_needs(props: dict[str, Any], ctx: Context, what: str) -> list[str]:
    f = ctx.facts.items.get(props.get("fact_key") or "")
    if f is None:
        return [f"fact_key {props.get('fact_key')!r} is not in the FACTS"]
    if what == "number" and f.number is None:
        return [f"fact {f.key!r} has no number to show"]
    if what == "series" and not f.values:
        return [f"fact {f.key!r} has no series to draw"]
    return []


@register("stat_chip", StatChipProps,
          "a big number that pops in with a caption, e.g. downloads or forks. props: "
          "fact_key (a FACTS key), optional label/prefix/suffix. About 2.5 s.",
          default_seconds=2.5)
def _stat_chip(props: dict[str, Any], ctx: Context) -> list[str]:
    return _fact_needs(props, ctx, "any")


@register("star_counter", StarCounterProps,
          "the repo's star count ticking up from zero to the real number. props: "
          "fact_key (default 'github.stars'), optional sub. About 4 s.",
          default_seconds=4.0)
def _star_counter(props: dict[str, Any], ctx: Context) -> list[str]:
    return _fact_needs(props, ctx, "number")


@register("contrib_graph", ContribGraphProps,
          "weekly commit activity over the last year, bars growing left to right. "
          "props: fact_key (default 'github.commit_activity'). About 5 s.",
          default_seconds=5.0)
def _contrib_graph(props: dict[str, Any], ctx: Context) -> list[str]:
    return _fact_needs(props, ctx, "series")


LOGIN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,37}[a-z0-9])?$", re.I)


@register("logo_wall", LogoWallProps,
          "avatars of GitHub orgs that use the project, popping in as a grid. props: "
          "orgs (2-12 GitHub logins named in the source). About 4 s.",
          default_seconds=4.0)
def _logo_wall(props: dict[str, Any], ctx: Context) -> list[str]:
    orgs = props.get("orgs") or []
    if not 2 <= len(orgs) <= 12:
        return ["a logo wall needs 2-12 orgs"]
    if bad := [o for o in orgs if not LOGIN.fullmatch(str(o))]:
        return [f"{bad[0]!r} is not a GitHub login"]
    if ctx.brief and (missing := [o for o in orgs if str(o).lower() not in ctx.brief.lower()]):
        return [f"{missing[0]!r} is not named in the source; only show users the source names"]
    return []


@register("claim_card", ClaimCardProps,
          "a sentence quoted from the source, in a card, with where it appears. props: "
          "quote (copied exactly), optional source_ref. About 4.5 s.",
          default_seconds=4.5)
def _claim_card(props: dict[str, Any], ctx: Context) -> list[str]:
    quote = re.sub(r"\s+", " ", str(props.get("quote") or "")).strip()
    if not quote:
        return ["a claim card needs a quote"]
    if ctx.brief and re.sub(r"\s+", " ", ctx.brief).lower().find(quote.lower()) < 0:
        return ["the quote must be copied exactly from the source"]
    return []


LOGO = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,37}[a-z0-9])?$", re.I)


async def fetch_logos(orgs: list[str], dest: Path, cache: Path | None = None) -> list[str]:
    """Download GitHub avatars for `dest` (the job's public dir), reusing a shared
    cache; returns the logins that failed so the caller can drop them with a note."""
    dest.mkdir(parents=True, exist_ok=True)
    missing = []
    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as http:
        for org in dict.fromkeys(orgs):
            if not LOGO.fullmatch(org):
                missing.append(org)
                continue
            out = dest / f"{org.lower()}.png"
            cached = cache / f"{org.lower()}.png" if cache else None
            try:
                if cached and cached.exists():
                    out.write_bytes(cached.read_bytes())
                    continue
                r = await http.get(f"https://github.com/{org}.png?size=128",
                                   headers={"User-Agent": UA})  # fmt: skip
                if r.status_code != 200 or not r.content:
                    missing.append(org)
                    continue
                out.write_bytes(r.content)
                if cached:
                    cached.parent.mkdir(parents=True, exist_ok=True)
                    cached.write_bytes(r.content)
            except Exception:
                missing.append(org)
    return missing
