# Meta AI tooling for Cueframe (research, 2026-10-26)

Source: Meta Model API docs, https://dev.meta.ai/docs (overview, models, sdks, search-grounding,
tool-calling, tool-search, computer-use, video-understanding, media-segmentation, agent-frameworks,
pricing-rate-limits).

## 1. There is no separate "Meta Agents SDK". Use the OpenAI Agents SDK, pointed at Meta

Meta Model API is wire-compatible with the OpenAI APIs (Responses + Chat Completions) and with the
Anthropic Messages API:

- Base URL `https://api.meta.ai/v1`
- Authentication: `Authorization: Bearer $MODEL_API_KEY` (keys look like `LLM_<id>_<secret>`)

Meta's own agent guide documents two frameworks:

- the Claude Agent SDK, via `ANTHROPIC_BASE_URL=https://api.meta.ai`
- the OpenAI Codex app-server, via the Responses API

Meta does not mention the OpenAI Agents SDK by name. However, `openai-agents` 0.24.0 talks to the
Responses API, and its hosted tools serialize as `{"type":"web_search"}` and `{"type":"computer"}`.
Those are exactly the tool types Meta documents, so it should work unchanged:

```python
from openai import AsyncOpenAI
from agents import (
    Agent,
    Runner,
    WebSearchTool,
    function_tool,
    set_default_openai_client,
    set_default_openai_api,
    set_tracing_disabled,
)

set_default_openai_client(
    AsyncOpenAI(base_url="https://api.meta.ai/v1", api_key=MODEL_API_KEY), use_for_tracing=False
)
set_default_openai_api("responses")
set_tracing_disabled(True)  # traces would otherwise be uploaded to OpenAI

agent = Agent(
    name="Researcher",
    model="muse-spark-1.3",
    instructions="Find the official docs for the app the user names; cite them.",
    tools=[WebSearchTool()],
)
print((await Runner.run(agent, "How do I export a vertical video in Shotcut?")).final_output)
```

Not yet verified live: this needs a `MODEL_API_KEY`. The first spike should confirm the following
work against Meta:

- `WebSearchTool`
- function tools
- handoffs
- `previous_response_id`

## 2. Models

| Model | Use in Cueframe | Price (per 1M tokens in / out) |
| --- | --- | --- |
| `muse-wonder-1.0` (preview) | Planner / computer-use driver (long-horizon, sub-agents) | $4 / $20 |
| `muse-spark-1.3` | Research, step verification, chat | $1.25 / $4.25 |
| `muse-spark-1.3-contributor` | Cheap dev/testing (Meta may train on your data) | $0.10 / $0.20 |
| `sam-3.1` | Segment / track UI elements in images and video | per image/frame |
| Muse Image | Tutorial thumbnails / title cards | $0.01 per image |
| Muse Voice Transcribe | User speaks the goal; speaker attribution | per hour of audio |

All Muse text models accept text, image, video, audio and PDF input, and have a 1M-token context
window. Video input works by uploading the file with `purpose="user_data"` and passing it as
`input_file`.

## 3. Built-in tools (server-side)

- `web_search`: grounded answers with `url_citation` annotations. Use
  `include:["web_search_call.results"]` to get the raw hits. Costs $2.50 per 1,000 queries.
- `tool_search`: defers large tool catalogs (e.g. MCP tools) so only the needed schemas are loaded.
  Supports hosted and client-executed modes.
- `computer`: an observe-act loop. The model returns click/type/scroll actions; we run them in our
  sandbox VM (Playwright, xdotool) and send back a screenshot.
- Plus developer function tools, parallel tool calls, structured output, reasoning effort from
  `minimal` to `max`, and prompt caching.

Not offered by Meta (we provide these ourselves):

- code interpreter
- hosted file_search / vector store
- hosted MCP
- **text-to-speech** (narration needs another provider, e.g. ElevenLabs or open-source TTS)

## 4. Mapping to the judging criteria

**Use of Meta AI Tooling (25%):** go beyond a chat wrapper.

1. **Docs-first research:** `web_search` with citations shown in the run panel.
2. **Sandbox driver:** the `computer` tool on `muse-wonder-1.0` performs each step in a real VM.
3. **Proof that each step happened (the unexpected capability):** after every step, Muse Spark
   *watches the recorded clip* (video understanding) and answers in structured output whether the
   expected result is visible, giving `verified` or `failed` honestly.
4. **SAM 3.1:** tracks the clicked button/menu across the clip to auto-generate zoom and highlight
   overlays in the tutorial video.
5. **Muse Voice Transcribe:** voice input for the goal. **Muse Image:** cover thumbnail.
6. **Tiered models:** Wonder for planning/acting, Spark for verification, contributor variants in
   development. Cost per tutorial is shown in the UI.

**Technical depth:** the existing job/event/WebSocket backbone, persisted traces, retries, and an
API usable without the UI (Postman/MCP).

**Demo:** live run of goal → steps verified in real time → final narrated video.

**Impact:** personalised software tutorials built on the user's own files.

## 5. Alternatives considered

- **Llama Stack:** renamed **OGX** and now model-agnostic, with an OpenAI-compatible Responses
  server. It is no longer a Meta-branded tool, so it scores poorly on "Meta tooling".
- **Muse Code CLI:** Meta's own coding agent. Good for dev work, but not something we embed.
- **Claude Agent SDK → Meta:** officially documented by Meta. Gives built-in file/shell tools, but
  it is a Claude-branded SDK.
