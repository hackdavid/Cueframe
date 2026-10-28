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


def _prose(text: str) -> str:
    """PDF lines joined back into running text: 'recur-\nrence' -> 'recurrence'."""
    text = re.sub(r"(\w)-\n(?=[a-z]+[\s.,;:!?)])", r"\1", text.strip())
    text = re.sub(r"(\w)-\n(?=\w)", r"\1-", text)
    return re.sub(r"[ \t]*\n[ \t]*(?!\n)", " ", re.sub(r"\n{2,}", "\x00", text)).replace(
        "\x00", "\n\n"
    )


def _spans(para: str) -> str:
    """One element per sentence (long ones per clause): the browser finds words by the
    element that holds them, and only an element's first 120 characters count."""
    out = []
    for s in re.split(r"(?<=[.!?])\s+", para.strip()):
        for b in re.split(r"(?<=[,;:])\s+", s) if len(s) > 110 else [s]:
            words = b.split()
            step = 14 if len(b) > 110 else len(words) or 1
            out += [html.escape(" ".join(words[i : i + step])) for i in range(0, len(words), step)]
    out = [f"<span>{b}</span>" for b in out]
    return " ".join(out)


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
        body = "\n\n".join(f"[page {i}] {_prose(t)}" for i, t in enumerate(pages, 1) if t.strip())
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


def readable(name: str, kind: str, text: str) -> str:
    """The document's own text as a plain page the browser can scroll and point at: a PDF
    viewer's words can't be found or clicked, a page's can. One heading per page/slide."""
    parts = re.split(r"\[(page|slide) (\d+)\] ?", text)
    body = [f"<p>{_spans(parts[0])}</p>"] if parts[0].strip() else []
    for i in range(1, len(parts) - 2, 3):
        paras = "".join(f"<p>{_spans(p)}</p>" for p in parts[i + 2].split("\n\n") if p.strip())
        body.append(f"<h2>{parts[i].title()} {parts[i + 1]}</h2>{paras}")
    return ("<!doctype html><meta charset=utf-8>"
            f"<title>{html.escape(name)}</title><style>body{{font:26px/1.5 sans-serif;"
            "max-width:1500px;margin:40px auto;padding:0 40px;color:#111}h1{font-size:40px}"
            "h2{margin-top:48px;border-top:2px solid #ccc;padding-top:16px}p{margin:0 0 20px}"
            f"</style><h1>{html.escape(name)}</h1><p><i>Text of the {html.escape(kind)}, "
            "as uploaded</i></p>" + "".join(body))  # fmt: skip


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
    for f in list(files):
        got = read_file(Path(f["path"]), f["name"])
        if not got:
            continue
        names.append(f["name"])
        show = f"open it in Chromium at file:///work/{f['name']} or show it in the terminal"
        if not got[0].startswith("text"):
            copy = re.sub(r"[^\w.-]", "_", Path(f["name"]).stem) + "-text.html"
            path = Path(f["path"]).with_name(copy)
            path.write_text(readable(f["name"], got[0].split(",")[0], got[1]))
            files.append({"name": copy, "kind": "file", "content_type": "text/html",
                          "path": str(path)})  # fmt: skip
            show = (f"to show it, open its text page file:///work/{copy} (one 'Page N' or "
                    "'Slide N' heading each; scroll to a heading, or to 3-8 words copied exactly "
                    "from one sentence of the text below). The original can be opened at "
                    f"file:///work/{f['name']}#page=N, but words in Chromium's PDF viewer "
                    "can't be scrolled to or clicked")  # fmt: skip
        parts.append(f"Your file /work/{f['name']} ({got[0]}; {show}):\n{_clip(got[1])}")
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
