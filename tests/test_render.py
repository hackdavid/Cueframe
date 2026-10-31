"""The Remotion bundle cache: a cached bundle is reused as-is, with the job's own
assets copied into its public dir (never the other job's)."""

import tarfile

import pytest

from app.tutorial import render
from app.tutorial.render import _bundle, code_hash


def _fake_bundle_tgz(tmp_path) -> bytes:
    src = tmp_path / "src" / "bundle"
    (src / "public").mkdir(parents=True)
    (src / "index.html").write_text("<html>bundle</html>")
    out = tmp_path / "bundle.tar.gz"
    with tarfile.open(out, "w:gz") as tar:
        tar.add(src, arcname="bundle")
    return out.read_bytes()


@pytest.mark.anyio
async def test_cached_bundle_is_reused_and_gets_the_jobs_assets(tmp_path):
    data = _fake_bundle_tgz(tmp_path)
    work = tmp_path / "work"
    (work / "public" / "tid1").mkdir(parents=True)
    (work / "public" / "tid1" / "recording.mp4").write_bytes(b"job footage")

    fetched, saved = [], []

    async def fetch(name):
        fetched.append(name)
        return data

    async def save(name, path):
        saved.append(name)

    bundle = await _bundle(work, fetch, save)
    assert fetched == [f"bundle-{code_hash()}.tar.gz"]
    assert saved == []  # cache hit: nothing re-bundled, nothing re-saved
    assert (bundle / "index.html").read_text() == "<html>bundle</html>"
    assert (bundle / "public" / "tid1" / "recording.mp4").read_bytes() == b"job footage"
