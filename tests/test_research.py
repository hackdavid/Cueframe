import json

import httpx
import pytest

from app.agents import web
from app.agents.contracts import ResearchRequest
from app.agents.docs_research import DocsResearchAgent, render_markdown
from app.config import Settings
from app.llm import MetaClient

EXPORT = """<html><head><title>Export</title></head><body><nav><a href="/vertical">Vertical
video</a></nav><main><h1>Export</h1><p>Use the Export panel to render files.</p>
<p>Click Export File.</p></main></body></html>"""
VERTICAL = """<html><head><title>Vertical video</title></head><body><main><h1>Vertical video</h1>
<h2>Set a 9:16 video mode</h2><ol><li>Open Settings &gt; Video Mode.</li>
<li>Choose Vertical HD 30 fps.</li></ol><img src="/img/mode.png" alt="video mode menu">
</main></body></html>"""
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32


def web_transport() -> httpx.MockTransport:
    def handle(req: httpx.Request) -> httpx.Response:
        pages = {"/export": EXPORT, "/vertical": VERTICAL}
        if req.url.path in pages:
            return httpx.Response(200, html=pages[req.url.path])
        if req.url.path == "/img/mode.png":
            return httpx.Response(200, content=PNG, headers={"content-type": "image/png"})
        return httpx.Response(404)

    return httpx.MockTransport(handle)


def message(obj: dict) -> dict:
    return {"type": "message", "content": [{"type": "output_text", "text": json.dumps(obj)}]}


def meta_transport(assess_replies: list[dict], calls: list[dict]) -> httpx.MockTransport:
    def handle(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        calls.append(body)
        name = body["text"]["format"]["name"]
        output = []
        if name == "DocsDiscovery":
            output.append(
                {
                    "type": "web_search_call",
                    "action": {"type": "search", "query": "x"},
                    "results": [{"url": "https://forum.example.org/t/1", "title": "Forum"}],
                }
            )
            out = {
                "app": "ExampleCut",
                "official_domains": ["docs.example.com"],
                "urls": ["https://docs.example.com/export"],
            }
        elif name == "StepsDraft":
            out = assess_replies.pop(0)
        else:
            out = {
                "confidence": 0.55,
                "caveats": "May differ by version.",
                "steps": [
                    {
                        "index": 1,
                        "title": "Open settings",
                        "detail": "Settings menu.",
                        "source_url": "https://made.up/url",
                    }
                ],
            }
        output.append(message(out))
        usage = {"input_tokens": 1000, "output_tokens": 100}
        return httpx.Response(200, json={"id": "r", "output": output, "usage": usage})

    return httpx.MockTransport(handle)


def settings(tmp_path) -> Settings:
    return Settings(tmp_path, 1, 1, 0.1, False, meta_api_key="test")


async def run(tmp_path, replies: list[dict]) -> tuple:
    calls: list[dict] = []
    llm = MetaClient("test", transport=meta_transport(replies, calls), max_retries=0)
    async with httpx.AsyncClient(transport=web_transport()) as http:
        stages: list[str] = []
        result = await DocsResearchAgent(llm, settings(tmp_path), http).run(
            ResearchRequest(query="How do I export a vertical 9:16 video?"),
            lambda stage, label, value: stages.append(stage),
        )
    await llm.aclose()
    return result, calls, stages


@pytest.mark.anyio
async def test_follows_links_then_answers_from_docs(tmp_path):
    vertical = "https://docs.example.com/vertical"
    replies = [
        {
            "found": False,
            "confidence": 0.3,
            "steps": [],
            "missing": "video mode",
            "follow_links": [vertical, "https://not.offered/x"],
        },
        {
            "found": True,
            "confidence": 0.9,
            "missing": "",
            "follow_links": [],
            "steps": [
                {
                    "index": 1,
                    "title": "Set video mode",
                    "detail": "Settings > Video Mode",
                    "source_url": vertical,
                    "image_url": "https://docs.example.com/img/mode.png",
                },
                {
                    "index": 2,
                    "title": "Export",
                    "detail": "Click Export File",
                    "source_url": "https://hallucinated.example/x",
                    "image_url": "https://x/y.png",
                },
            ],
        },
    ]
    result, calls, stages = await run(tmp_path, replies)
    assert result.source == "docs" and result.confidence == 0.9
    assert result.pages_read == ["https://docs.example.com/export", vertical]
    assert result.steps[0].image_url == "https://docs.example.com/img/mode.png"
    assert result.steps[1].source_url is None and result.steps[1].image_url is None
    assert [t.stage for t in result.trace] == ["discover", "read", "assess", "read", "assess"]
    assert result.usage.input_tokens == 3000 and result.usage.web_searches == 1
    discover = calls[0]
    assert discover["model"] == "muse-spark-1.3-contributor"
    assert discover["max_tool_calls"] == 2 and discover["reasoning"] == {"effort": "low"}
    assert discover["tools"] == [{"type": "web_search", "search_context_size": "low"}]
    second = calls[2]["input"][0]["content"]
    assert any(c["type"] == "input_image" for c in second)
    assert "Vertical HD 30 fps" in second[0]["text"]
    assert stages[0] == "discover" and "Set video mode" in render_markdown(result)


@pytest.mark.anyio
async def test_falls_back_to_model_knowledge(tmp_path):
    replies = [
        {"found": False, "confidence": 0.2, "steps": [], "missing": "nothing", "follow_links": []}
    ]
    result, calls, _ = await run(tmp_path, replies)
    assert result.source == "model_knowledge" and result.confidence == 0.55
    assert result.steps[0].source_url is None
    assert calls[-1]["reasoning"] == {"effort": "high"} and "tools" not in calls[-1]
    assert "model's own knowledge" in render_markdown(result)


@pytest.mark.anyio
async def test_contract_violation_raises(tmp_path):
    from app.llm import LLMError

    replies = [{"found": "maybe"}]
    with pytest.raises(LLMError, match="StepsDraft contract violated"):
        await run(tmp_path, replies)


def test_parse_html_keeps_sections_images_links():
    page = web.parse_html("https://docs.example.com/vertical", VERTICAL)
    sec = next(s for s in page.sections if s.heading == "Set a 9:16 video mode")
    assert "- Choose Vertical HD 30 fps." in sec.text
    assert sec.images[0].url == "https://docs.example.com/img/mode.png"
    assert (
        web.parse_html("https://docs.example.com/export", EXPORT).links[0].url.endswith("/vertical")
    )


def test_api_requires_meta_key(client):
    res = client.post(
        "/api/research/steps",
        json={"query": "How do I export?"},
        headers={"Authorization": f"Bearer {client.token}"},
    )
    assert res.status_code == 503


@pytest.fixture
def anyio_backend():
    return "asyncio"
