"""Fetch, clean and rank documentation pages locally (no model tokens)."""

import base64
import math
import re
from collections import Counter
from urllib.parse import urldefrag, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup, Tag

from app.agents import UA
from app.agents.contracts import DocImage, PageCandidate, PageContent, Section
MAX_PAGE_BYTES = 3_000_000
MAX_IMAGE_BYTES = 1_500_000
DROP = ["script", "style", "noscript", "nav", "header", "footer", "aside", "form", "svg", "iframe"]
TEXT_TAGS = ["p", "li", "pre", "td", "dt", "dd", "figcaption", "blockquote"]
HEADINGS = {"h1", "h2", "h3", "h4"}
STOP = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "can",
        "do",
        "does",
        "for",
        "from",
        "how",
        "i",
        "in",
        "is",
        "it",
        "me",
        "my",
        "of",
        "on",
        "or",
        "the",
        "to",
        "what",
        "when",
        "where",
        "which",
        "with",
        "you",
        "your",
        "give",
        "short",
        "steps",
        "step",
        "cite",
        "official",
        "url",
        "using",
        "use",
    ]
)


def tokens(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+", text.lower()) if t not in STOP]


def bm25(query: str, docs: list[str], k1: float = 1.5, b: float = 0.75) -> list[float]:
    q = set(tokens(query))
    toks = [tokens(d) for d in docs]
    if not toks:
        return []
    avg = sum(map(len, toks)) / len(toks) or 1
    df = Counter(w for t in toks for w in set(t))
    n = len(toks)
    scores = []
    for t in toks:
        tf = Counter(t)
        s = 0.0
        for w in q & tf.keys():
            idf = math.log(1 + (n - df[w] + 0.5) / (df[w] + 0.5))
            s += idf * tf[w] * (k1 + 1) / (tf[w] + k1 * (1 - b + b * len(t) / avg))
        scores.append(s)
    return scores


def normalize_url(base: str, href: str) -> str | None:
    url = urldefrag(urljoin(base, href.strip()))[0]
    return url if urlparse(url).scheme in ("http", "https") else None


def same_site(a: str, b: str) -> bool:
    ha, hb = urlparse(a).hostname or "", urlparse(b).hostname or ""
    root = lambda h: ".".join(h.split(".")[-2:])  # noqa: E731
    return root(ha) == root(hb)


def parse_html(url: str, html: str) -> PageContent:
    soup = BeautifulSoup(html, "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else url
    links: dict[str, PageCandidate] = {}
    for a in soup.find_all("a", href=True):
        link = normalize_url(url, a["href"])
        text = a.get_text(" ", strip=True)
        if link and link != url and same_site(link, url) and text and link not in links:
            links[link] = PageCandidate(url=link, title=text[:120], origin="link")
    for tag in soup(DROP):
        tag.decompose()
    root = soup.find("main") or soup.find("article") or soup.body or soup
    sections: list[Section] = []
    cur = Section(heading=title)
    for el in root.find_all([*HEADINGS, *TEXT_TAGS, "img"]):
        if not isinstance(el, Tag):
            continue
        if el.name in HEADINGS:
            if cur.text or cur.images:
                sections.append(cur)
            cur = Section(heading=el.get_text(" ", strip=True)[:200] or title)
        elif el.name == "img":
            src = el.get("data-src") or el.get("src") or ""
            width = str(el.get("width") or "999")
            if src and not src.startswith("data:") and (not width.isdigit() or int(width) >= 64):
                img = normalize_url(url, str(src))
                if img:
                    cur.images.append(DocImage(url=img, alt=str(el.get("alt") or "")[:200]))
        elif not el.find_parent(TEXT_TAGS):
            text = el.get_text(" ", strip=True)
            if len(text) > 1:
                cur.text += ("- " if el.name == "li" else "") + text + "\n"
    if cur.text or cur.images:
        sections.append(cur)
    for s in sections:
        s.text = s.text[:4000]
    return PageContent(url=url, title=title[:200], sections=sections, links=list(links.values()))


async def fetch_page(http: httpx.AsyncClient, url: str) -> PageContent:
    res = await http.get(url, headers={"User-Agent": UA}, follow_redirects=True)
    res.raise_for_status()
    ctype = res.headers.get("content-type", "")
    if "html" not in ctype and "xml" not in ctype:
        raise ValueError(f"not an HTML page ({ctype or 'unknown type'})")
    if len(res.content) > MAX_PAGE_BYTES:
        raise ValueError("page too large")
    return parse_html(str(res.url), res.text)


async def fetch_image_data_url(http: httpx.AsyncClient, url: str) -> str | None:
    try:
        res = await http.get(url, headers={"User-Agent": UA}, follow_redirects=True)
    except httpx.HTTPError:
        return None
    ctype = res.headers.get("content-type", "").split(";")[0]
    ok = ctype in ("image/png", "image/jpeg", "image/webp", "image/gif")
    if res.status_code != 200 or not ok or len(res.content) > MAX_IMAGE_BYTES:
        return None
    return f"data:{ctype};base64," + base64.b64encode(res.content).decode()


def rank_candidates(query: str, cands: list[PageCandidate]) -> list[PageCandidate]:
    docs = [f"{c.title} {c.snippet} {urlparse(c.url).path.replace('/', ' ')}" for c in cands]
    order = sorted(zip(bm25(query, docs), range(len(cands)), strict=True), reverse=True)
    return [cands[i] for _, i in order]


def top_sections(
    query: str, pages: list[PageContent], budget_chars: int = 8000
) -> list[tuple[PageContent, Section]]:
    pairs = [(p, s) for p in pages for s in p.sections if s.text.strip()]
    scores = bm25(query, [f"{s.heading} {s.heading} {s.text}" for _, s in pairs])
    ranked = sorted(zip(scores, range(len(pairs)), strict=True), reverse=True)
    out, used = [], 0
    for score, i in ranked:
        size = len(pairs[i][1].text)
        if score <= 0 and out:
            break
        if used + size > budget_chars and out:
            continue
        out.append(pairs[i])
        used += size
    return out
