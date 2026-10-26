# Meta Model API: live test results (2026-10-26)

Tests used `MODEL_API_KEY` against `https://api.meta.ai/v1`. Scripts: `scripts/meta_api_smoke.py` and `scripts/meta_cua_smoke.py`.

`GET /v1/models` lists `muse-wonder-1.0`, `muse-spark-1.3` (plus `-contributor`), `muse-spark-1.2`, `muse-spark-1.1`, `muse-image-1.0`, `muse-voice-transcribe-1.0` and `sam-3.1`.
Muse Spark Open, Muse Glimmer and Muse Code are **not** on this key.

| Capability | Result | Notes |
| --- | --- | --- |
| Chat Completions, `muse-spark-1.3` | Works (2.9 s) | Even "pong" used about 330 reasoning tokens |
| Responses, `muse-wonder-1.0` | Works (1.6 s) | Defaults to `reasoning.effort: max` |
| `web_search` (Spark and Wonder) | Works (10 s) | Ran 7 searches and page opens; one `url_citation`; about 39k input tokens |
| Function calling (Spark and Wonder) | Works | Returns `function_call` |
| OpenAI Agents SDK 0.24.0 | Works | WebSearchTool plus a function tool, and a second turn via `previous_response_id`. Tracing must be off |
| Anthropic `/v1/messages` | Works | Returns `redacted_thinking`, then text. Needs a large `max_tokens`: at 200 only thinking came back |
| Image understanding | Works (3.7 s) | Correctly read the login screenshot |
| Files API, then video understanding | Works (6.2 s) | Read the 6 s clip with per-frame timestamps. It missed the final "admin typed" frame, so check the last frames separately |
| `computer` tool, `muse-spark-1.3` | **Works** | Full loop with Playwright: logged into Cueframe in 3 turns, about 23 s. Every action batch carried a `computer_action_requires_confirmation` safety check |
| `computer` tool, `muse-wonder-1.0` | **Fails** | `400 Unsupported tool type: 'computer'` |
| SAM 3.1, image | Works (0.9 s) | Box and mask for "button" |
| SAM 3.1, video (Files API `file_id`) | Works | "button" matched in 62 frames with a stable object id. "text field" and "username input field" returned 0 matches |
| Muse Image | Works (22 s) | Returns a WebP as `b64_json` |
| Muse Voice Transcribe `/v1/asr/transcribe` | Works | Requires mono s16le WAV at 16 or 24 kHz; stereo 44.1 kHz returns 400 |
| `background: true` responses | Accepted | Returned `queued` |
| Text to speech | Not offered | |
