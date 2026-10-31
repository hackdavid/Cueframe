"""Verified numbers about the video's subject (a GitHub repo, a Hugging Face model):
fetched once per job from public APIs, kept on the spec, and the ONLY numbers motion
overlays may show. The planner references facts by key (`fact_key`), never by value;
`validate_numbers` rejects anything else, the same way the concept board rejects
numbers that aren't in the source."""

import asyncio
import json
import os
import re
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel

from app.agents import UA

CACHE_TTL = 24 * 3600  # seconds; repeated videos about the same subject are free
BRIEF_LINES = 15  # the prompt never sees more than this many facts
SUBJECT = re.compile(r"[\w.-]+/[\w.-]+")
FROM_GOAL = {
    "github": re.compile(r"github\.com/([\w.-]+/[\w.-]+?)(?:\.git)?/?(?:[#?].*)?(?:\s|$)"),
    "hf": re.compile(r"huggingface\.co/(?!papers/|spaces/)([\w.-]+/[\w.-]+)"),
}
FROM_BRIEF = {
    "github": re.compile(r"GitHub repo ([\w.-]+/[\w.-]+)"),
    "hf": re.compile(r"huggingface\.co/(?!papers/|spaces/)([\w.-]+/[\w.-]+)"),
}
_NUM = re.compile(r"(?<![\w.,])(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?![\d,]*\d)")


def _canon(n: str) -> str:
    """Exact decimal form without trailing zeros: "90.20" -> "90.2", "1,000" -> "1000"."""
    d = Decimal(n.replace(",", ""))
    return format(d.normalize() if d != d.to_integral() else d.to_integral(), "f")


def _nums(s: str) -> set[str]:
    return {_canon(n) for n in _NUM.findall(s)}


class Fact(BaseModel):
    key: str  # "github.stars", "hf.downloads", "github.lang.python_pct"
    label: str  # "GitHub stars"
    value: str  # display string, exactly as shown or spoken: "12,345"
    number: float | None = None
    values: list[float] | None = None  # a series (commit activity, languages)
    source: str = ""
    as_of: str = ""


class Facts(BaseModel):
    subject: str = ""  # "owner/name" repo or model id
    items: dict[str, Fact] = {}

    def numbers(self) -> set[str]:
        """Every canonical number a fact carries; the only numbers overlays may show."""
        out = set()
        for f in self.items.values():
            out |= _nums(f.value)
            if f.number is not None:
                out.add(_canon(repr(f.number)))
            out |= {_canon(repr(v)) for v in f.values or []}
        return out


def brief_block(f: Facts) -> str:
    """The facts as the planner reads them: one line each, newest subject first."""
    lines = [
        f"{x.key}: {x.value} ({x.label}, as of {x.as_of})" for x in f.items.values()
    ]
    return "\n".join(lines[:BRIEF_LINES])


SMALL = {"0", "1", "2", "3", "4", "5"}  # counting words like "top 3" need no fact


def validate_numbers(component: str, props: dict[str, Any], facts: Facts) -> list[str]:
    """Numbers written straight into props that no fact carries. Props whose value is a
    fact key or a slug list are exempt (their numbers resolve from the facts blob)."""
    have = facts.numbers()
    out = []
    for k, v in props.items():
        if k in ("fact_key", "orgs", "quote", "source_ref") or isinstance(v, bool):
            continue
        nums = {_canon(repr(v))} if isinstance(v, int | float) else _nums(str(v))
        for n in sorted(nums - have):
            if n not in SMALL:
                out.append(f"{component}: the number {n} in {k!r} is not in the FACTS")
    return out


def _fact(key: str, label: str, n: float | int | None, source: str, as_of: str,
          values: list[float] | None = None, suffix: str = "") -> Fact | None:  # fmt: skip
    if n is None:
        return None
    value = f"{int(n):,}" if float(n).is_integer() else f"{n:,.1f}"
    return Fact(key=key, label=label, value=value + suffix, number=float(n),
                values=values, source=source, as_of=as_of)  # fmt: skip


async def github_facts(http: httpx.AsyncClient, repo: str) -> Facts:
    """Stars, forks, watchers, issues, contributors, languages, commit activity and the
    'Used by' count for a repo. Public endpoints; a failed one just leaves its keys out."""
    if not SUBJECT.fullmatch(repo):
        return Facts()
    today = datetime.now(UTC).date().isoformat()
    api = f"https://api.github.com/repos/{repo}"
    headers = {"User-Agent": UA, "Accept": "application/vnd.github+json"}
    if t := os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {t}"
    meta, langs, contribs, activity, page = await asyncio.gather(
        http.get(api, headers=headers),
        http.get(f"{api}/languages", headers=headers),
        http.get(f"{api}/contributors", params={"per_page": 1, "anon": "true"},
                 headers=headers),  # fmt: skip
        http.get(f"{api}/stats/commit_activity", headers=headers),
        http.get(f"https://github.com/{repo}", headers={"User-Agent": UA}),
        return_exceptions=True,
    )
    facts = Facts(subject=repo)
    if isinstance(meta, httpx.Response) and meta.status_code == 200:
        d = meta.json()
        for key, label, n in (
            ("github.stars", "GitHub stars", d.get("stargazers_count")),
            ("github.forks", "forks", d.get("forks_count")),
            ("github.watchers", "watchers", d.get("subscribers_count")),
            ("github.open_issues", "open issues", d.get("open_issues_count")),
        ):
            if f := _fact(key, label, n, api, today):
                facts.items[key] = f
    if isinstance(langs, httpx.Response) and langs.status_code == 200:
        total = sum(langs.json().values()) or 1
        for name, share in sorted(langs.json().items(), key=lambda x: -x[1])[:4]:
            pct = round(100 * share / total, 1)
            slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
            facts.items[f"github.lang.{slug}_pct"] = Fact(
                key=f"github.lang.{slug}_pct", label=f"{name} share of the code",
                value=f"{pct}%", number=pct, source=f"{api}/languages", as_of=today,
            )
    if isinstance(contribs, httpx.Response) and contribs.status_code == 200:
        n = _last_page(contribs) or len(contribs.json())
        if f := _fact("github.contributors", "contributors", n, f"{api}/contributors", today):
            facts.items["github.contributors"] = f
    if isinstance(activity, httpx.Response):
        if activity.status_code == 202:  # GitHub is still computing it: one retry
            await asyncio.sleep(1.5)
            activity = await http.get(f"{api}/stats/commit_activity", headers=headers)
        if isinstance(activity, httpx.Response) and activity.status_code == 200:
            weeks = [w.get("total", 0) for w in activity.json()]
            if weeks:
                facts.items["github.commit_activity"] = Fact(
                    key="github.commit_activity", label="weekly commits, last year",
                    value=f"{sum(weeks):,}", number=float(sum(weeks)),
                    values=[float(w) for w in weeks],
                    source=f"{api}/stats/commit_activity", as_of=today,
                )
    if isinstance(page, httpx.Response) and page.status_code == 200:
        if m := re.search(rf"/{re.escape(repo)}/network/dependents[^>]*>\s*(?:<[^>]+>\s*)*"
                          r"([\d,]+)", page.text):  # fmt: skip
            n = int(m.group(1).replace(",", ""))
            if f := _fact("github.used_by", "repos that depend on it", n,
                          f"github.com/{repo}", today):  # fmt: skip
                facts.items["github.used_by"] = f
    return facts


def _last_page(r: httpx.Response) -> int | None:
    """A collection's size from the Link header of a per_page=1 response."""
    m = re.search(r'[?&]page=(\d+)>;\s*rel="last"', r.headers.get("link", ""))
    return int(m.group(1)) if m else None


async def hf_model_facts(http: httpx.AsyncClient, model_id: str) -> Facts:
    if not SUBJECT.fullmatch(model_id):
        return Facts()
    today = datetime.now(UTC).date().isoformat()
    url = f"https://huggingface.co/api/models/{model_id}"
    r = await http.get(url, headers={"User-Agent": UA})
    if r.status_code != 200:
        return Facts()
    d, facts = r.json(), Facts(subject=model_id)
    for key, label, n in (
        ("hf.likes", "Hugging Face likes", d.get("likes")),
        ("hf.downloads", "downloads last month", d.get("downloads")),
    ):
        if f := _fact(key, label, n, url, today):
            facts.items[key] = f
    return facts


def _slug(subject: str) -> str:
    return re.sub(r"[^\w.-]+", "_", subject)


def _cache(data_dir: Path | None, key: str) -> Path | None:
    return data_dir / "tutorial-assets" / "facts" / f"{_slug(key)}.json" if data_dir else None


def load_cached(data_dir: Path | None, key: str) -> Facts | None:
    """Fresh cached facts for `key` ('github:owner/name'), else None."""
    path = _cache(data_dir, key)
    if not path or not path.exists():
        return None
    try:
        if path.stat().st_mtime < datetime.now(UTC).timestamp() - CACHE_TTL:
            return None
        return Facts.model_validate_json(path.read_text())
    except Exception:
        return None


def save_cached(data_dir: Path | None, key: str, facts: Facts) -> None:
    if not facts.items or not (path := _cache(data_dir, key)):
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(facts.model_dump_json())
    except OSError:
        pass


def _subject(goal: str, brief: str) -> tuple[str, str] | None:
    """(kind, owner/name) for a repo or model the request is about, else None."""
    for text, pats in ((goal, FROM_GOAL), (brief, FROM_BRIEF)):
        for kind, pat in pats.items():
            if m := pat.search(text):
                return kind, m.group(1)
    return None


async def collect(goal: str, brief: str, data_dir: Path | None = None) -> Facts:
    """The subject's facts, from the short-lived cache when fresh, else fetched."""
    if not (found := _subject(goal, brief)):
        return Facts()
    kind, subject = found
    if cached := load_cached(data_dir, f"{kind}:{subject}"):
        return cached
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as http:
            facts = await (github_facts if kind == "github" else hf_model_facts)(http, subject)
    except Exception:
        return Facts()
    save_cached(data_dir, f"{kind}:{subject}", facts)
    return facts
