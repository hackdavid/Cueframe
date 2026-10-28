"""The user's own sources: uploaded files (PDF, slides, documents, text and code) and
links in the message (a GitHub repo, a paper, any public web page) become the facts a
video is built on, the same way a trending item's page does. Read once per request and
never kept beyond that video."""

import asyncio
import html
import io
import ipaddress
import re
import socket
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from app.agents.trending import Tools
from app.agents.web import UA
from app.jobs import JobContext

CHARS = 9000  # per source, so a few sources fit the planner prompt
TEXT_EXT = {".txt", ".md", ".markdown", ".rst", ".csv", ".json", ".yaml", ".yml", ".toml",
            ".py", ".js", ".ts", ".tsx", ".go", ".rs", ".java", ".c", ".cpp", ".h", ".sh",
            ".html", ".css", ".sql", ".ipynb"}  # fmt: skip
URL = re.compile(r"https://[^\s<>()\"']+")
GITHUB = re.compile(r"https://github\.com/([\w.-]+/[\w.-]+?)(?:\.git)?/?(?:[#?].*)?$")
PAPER = re.compile(r"https://(?:huggingface\.co/papers|arxiv\.org/(?:abs|pdf))/(\d{4}\.\d{4,5})")


def _clip(text: str) -> str:
    text = re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n\n", text)).strip()
    return text if len(text) <= CHARS else text[:CHARS] + "\n[... cut]"


def _xml_text(data: bytes, tag: str) -> str:
    found = re.findall(rf"<{tag}>([^<]*)</{tag}>", data.decode("utf-8", "ignore"))
    return " ".join(html.unescape(t) for t in found)


def read_file(path: Path, name: str) -> tuple[str, str] | None:
    """(what it is, its text) for a file whose words can be read, else None."""
    ext = Path(name).suffix.lower()
    data = path.read_bytes()
    if ext == ".pdf":
        from pypdf import PdfReader

        try:
            pdf = PdfReader(io.BytesIO(data))
            pages = [p.extract_text() or "" for p in pdf.pages[:40]]
        except Exception:
            return None
        body = "\n\n".join(f"[page {i}] {t.strip()}" for i, t in enumerate(pages, 1) if t.strip())
        return (f"PDF, {len(pdf.pages)} pages", body) if body else None
    if ext in (".pptx", ".docx"):
        try:
            z = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile:
            return None
        if ext == ".docx":
            return "Word document", _xml_text(z.read("word/document.xml"), "w:t")
        slides = sorted((n for n in z.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)),
                        key=lambda n: int(re.findall(r"\d+", n)[0]))  # fmt: skip
        body = "\n\n".join(f"[slide {i}] {_xml_text(z.read(n), 'a:t')}"
                           for i, n in enumerate(slides, 1))  # fmt: skip
        return f"slides, {len(slides)} slides", body
    if ext in TEXT_EXT or not ext:
        text = data.decode("utf-8", "ignore")
        return ("text file", text) if text.strip() else None
    return None


def _public(url: str) -> bool:
    """Only public https hosts: never the server's own network."""
    host = urlparse(url).hostname or ""
    try:
        addrs = {a[4][0] for a in socket.getaddrinfo(host, 443)}
    except OSError:
        return False
    return bool(addrs) and all(ipaddress.ip_address(a).is_global for a in addrs)


async def read_link(http: httpx.AsyncClient, url: str) -> tuple[str, str] | None:
    """(what it is, its facts) for a GitHub repo, a paper or a public web page."""
    tools = Tools(http)
    if m := GITHUB.match(url):
        r = await tools.read_github_repo(m.group(1))
        if isinstance(r, dict):
            return (f"GitHub repo {r['repo']}, page to open: {r['url']}",
                    f"About: {r['about']}\nREADME headings: {'; '.join(r['readme_headings'])}"
                    f"\n\n{r['readme']}")  # fmt: skip
    if m := PAPER.match(url):
        p = await tools.read_paper(m.group(1))
        if isinstance(p, dict):
            return (f"paper, page to open: {p.get('hf_page') or url}",
                    "\n".join(f"{k}: {v}" for k, v in p.items() if v))  # fmt: skip
    if not await asyncio.to_thread(_public, url):
        return None
    r = await http.get(url, headers={"User-Agent": UA})
    if r.status_code != 200 or "html" not in r.headers.get("content-type", ""):
        return None
    soup = BeautifulSoup(r.text[:2_000_000], "html.parser")
    for t in soup(["script", "style", "nav", "footer", "noscript", "svg"]):
        t.decompose()
    title = soup.title.get_text(" ", strip=True) if soup.title else url
    heads = [h.get_text(" ", strip=True) for h in soup.find_all(["h1", "h2", "h3"])][:25]
    body = soup.get_text("\n", strip=True)
    return (f"web page '{title}', page to open: {url}",
            f"Headings: {'; '.join(heads)}\n\n{body}")  # fmt: skip


async def brief(ctx: JobContext, goal: str, files: list[dict[str, Any]]) -> str:
    """Grounding from the user's own files and links, '' when there are none to read."""
    parts, names = [], []
    for f in files:
        got = read_file(Path(f["path"]), f["name"])
        if got:
            parts.append(f"Your file /work/{f['name']} ({got[0]}; open it in the sandbox with "
                         f"Chromium at file:///work/{f['name']} or show it in the terminal):\n"
                         f"{_clip(got[1])}")  # fmt: skip
            names.append(f["name"])
    links = list(dict.fromkeys(u.rstrip(".,;:!?") for u in URL.findall(goal)))[:3]
    if links:
        ctx.phase("research", "Reading your links")
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as http:
            for url in links:
                try:
                    got = await read_link(http, url)
                except Exception as exc:
                    ctx.thinking("Sources", f"Couldn't read {url}: {str(exc)[:160]}")
                    continue
                if got:
                    parts.append(f"Your link {url} ({got[0]}):\n{_clip(got[1])}")
                    names.append(url)
    if not parts:
        return ""
    ctx.thinking("Sources", f"Using {', '.join(names)} as the source")
    return (
        "The user's own sources (use only facts from these; open the page or file "
        "they come from to show them):\n\n" + "\n\n".join(parts)
    )
