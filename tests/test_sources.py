import asyncio
import io
import zipfile

from app.tutorial import planner, sources


class Ctx:
    def __init__(self):
        self.notes = []

    def phase(self, *a):
        pass

    def thinking(self, title, text):
        self.notes.append(text)


def _pptx(path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for i, t in ((2, "Results: 3x faster"), (1, "Why caches matter")):
            z.writestr(f"ppt/slides/slide{i}.xml", f"<p:sld><a:t>{t}</a:t></p:sld>")
    path.write_bytes(buf.getvalue())


def _pdf(path, text):
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
            b"/Resources << /Font << /F1 5 0 R >> >> >>",
            b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]  # fmt: skip
    out, offs = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offs.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offs)
    out += b"trailer << /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF" % (len(objs) + 1, xref)
    path.write_bytes(out)


def test_files_are_read_as_the_source(tmp_path):
    _pdf(tmp_path / "paper.pdf", "Sparse attention cuts memory by 40 percent")
    _pptx(tmp_path / "deck.pptx")
    (tmp_path / "notes.md").write_text("# Plan\nShip on Friday")
    (tmp_path / "photo.jpg").write_bytes(b"\xff\xd8")
    kind, text = sources.read_file(tmp_path / "paper.pdf", "paper.pdf")
    assert kind == "PDF, 1 pages" and "40 percent" in text
    kind, text = sources.read_file(tmp_path / "deck.pptx", "deck.pptx")
    assert kind == "slides, 2 slides"
    assert text.index("[slide 1] Why caches") < text.index("[slide 2] Results: 3x")
    assert sources.read_file(tmp_path / "photo.jpg", "photo.jpg") is None

    files = [{"name": n, "path": str(tmp_path / n)} for n in ("paper.pdf", "notes.md", "photo.jpg")]
    ctx = Ctx()
    out = asyncio.run(sources.brief(ctx, "Make a reel about this paper", files))
    assert "file:///work/paper.pdf" in out and "Ship on Friday" in out and "photo.jpg" not in out
    assert asyncio.run(sources.brief(Ctx(), "Make a reel about caching", [])) == ""


def test_links_are_read_and_private_hosts_refused(monkeypatch):
    async def repo(self, name):
        return {
            "repo": name,
            "url": f"https://github.com/{name}",
            "about": "Fast KV store",
            "readme_headings": ["Install", "Benchmarks"],
            "readme": "10x fewer writes",
        }

    monkeypatch.setattr(sources.Tools, "read_github_repo", repo)
    out = asyncio.run(sources.brief(Ctx(), "Reel about https://github.com/acme/kv.", []))
    assert "GitHub repo acme/kv" in out and "10x fewer writes" in out
    assert not sources._public("https://localhost/")
    assert not sources._public("https://169.254.169.254/")


def test_plans_may_open_attached_files_only():
    assert not planner.BAD_URL.search("file:///work/paper.pdf")
    assert planner.BAD_URL.search("file:///etc/passwd")
    assert planner.BAD_URL.search("file:///work/../etc/passwd")
