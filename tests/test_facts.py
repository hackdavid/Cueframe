"""Facts: verified numbers about the subject; the only numbers overlays may show."""

import json
import os
import time

import httpx
import pytest

from app.tutorial import facts
from app.tutorial.facts import Fact, Facts


def _github_payload(url: str, headers: dict) -> httpx.Response:
    if url == "https://api.github.com/repos/o/r":
        return httpx.Response(200, json={
            "stargazers_count": 12345, "forks_count": 890, "subscribers_count": 123,
            "open_issues_count": 45})  # fmt: skip
    if "/languages" in url:
        return httpx.Response(200, json={"Python": 9020, "Go": 980})
    if "/contributors" in url:
        return httpx.Response(200, json=[{}], headers={
            "link": '<https://api.github.com/r?page=57>; rel="last"'})  # fmt: skip
    if "/stats/commit_activity" in url:
        return httpx.Response(200, json=[{"total": 3, "week": 1}, {"total": 5, "week": 2}])
    if url == "https://github.com/o/r":
        return httpx.Response(200, text='<a href="/o/r/network/dependents">1,234</a>')
    return httpx.Response(404)


def _client(handler) -> httpx.AsyncClient:
    async def _send(request):
        return handler(str(request.url), dict(request.headers))
    return httpx.AsyncClient(transport=httpx.MockTransport(_send))


@pytest.mark.anyio
async def test_github_facts_from_public_api():
    f = await facts.github_facts(_client(_github_payload), "o/r")
    assert f.subject == "o/r"
    assert f.items["github.stars"].value == "12,345"
    assert f.items["github.stars"].number == 12345
    assert f.items["github.contributors"].number == 57  # from the Link header
    assert f.items["github.commit_activity"].values == [3.0, 5.0]
    assert f.items["github.used_by"].value == "1,234"
    assert f.items["github.lang.python_pct"].value == "90.2%"


@pytest.mark.anyio
async def test_failed_endpoints_leave_keys_out():
    f = await facts.github_facts(_client(lambda u, h: httpx.Response(404)), "o/r")
    assert f.items == {}


@pytest.mark.anyio
async def test_hf_model_facts():
    def handler(url, headers):
        return httpx.Response(200, json={"likes": 321, "downloads": 456789})
    f = await facts.hf_model_facts(_client(handler), "o/m")
    assert f.items["hf.downloads"].value == "456,789"
    assert f.items["hf.likes"].number == 321


def test_brief_block_stays_short():
    f = Facts(subject="o/r", items={
        f"k{i}": Fact(key=f"k{i}", label="l", value=str(i)) for i in range(30)})
    assert len(facts.brief_block(f).splitlines()) <= facts.BRIEF_LINES


def _facts() -> Facts:
    return Facts(subject="o/r", items={
        "github.stars": Fact(key="github.stars", label="GitHub stars", value="12,345",
                             number=12345),
        "github.lang.python_pct": Fact(key="github.lang.python_pct", label="Python share",
                                       value="90.2%", number=90.2),
    })  # fmt: skip


def test_validate_numbers_accepts_fact_numbers():
    # "90.20%" written by hand canonicalizes to the fact's 90.2
    assert facts.validate_numbers("stat_chip", {"label": "90.20% Python"}, _facts()) == []
    assert facts.validate_numbers("stat_chip", {"fact_key": "github.stars"}, _facts()) == []


def test_validate_numbers_rejects_invented_numbers():
    out = facts.validate_numbers("stat_chip", {"label": "50,000 stars"}, _facts())
    assert out and "50000" in out[0]


def test_validate_numbers_skips_slugs_and_quotes():
    props = {"orgs": ["org3", "dbt-labs"], "quote": "速度 2x faster, page 3"}
    assert facts.validate_numbers("logo_wall", props, Facts()) == []


def test_subject_detection():
    assert facts._subject("make a video about https://github.com/o/r please", "") == (
        "github", "o/r")
    assert facts._subject("", "Your link x (GitHub repo o/r, page to open: y)") == (
        "github", "o/r")
    assert facts._subject("see https://huggingface.co/o/m", "") == ("hf", "o/m")
    assert facts._subject("see https://huggingface.co/papers/2510.12345", "") is None
    assert facts._subject("make a video about my photos", "") is None


def test_cache_roundtrip_and_ttl(tmp_path):
    f = _facts()
    facts.save_cached(tmp_path, "github:o/r", f)
    assert facts.load_cached(tmp_path, "github:o/r").items["github.stars"].number == 12345
    path = tmp_path / "tutorial-assets" / "facts" / "github_o_r.json"
    os.utime(path, (time.time() - facts.CACHE_TTL - 10,) * 2)
    assert facts.load_cached(tmp_path, "github:o/r") is None


@pytest.mark.anyio
async def test_collect_uses_cache_without_network(tmp_path):
    facts.save_cached(tmp_path, "github:o/r", _facts())
    got = await facts.collect("https://github.com/o/r", "", tmp_path)
    assert got.items["github.stars"].number == 12345
