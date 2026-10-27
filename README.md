# Cueframe

**Describe what you want to do in any software. Cueframe researches the official docs, does it for
real in a sandbox, checks every step, and replies with a verified, step-by-step tutorial.**

Cueframe is a chat-first agent platform built on **Meta's models** (Muse Spark, Muse Wonder,
SAM 3.1, Muse Image, Muse Voice Transcribe). Its design follows three ideas:

- **The API is the product.** Every browser action is a REST call or a WebSocket frame. Postman,
  scripts or an MCP server can drive exactly the same agent.
- **Long work never blocks the chat.** Jobs run in the background, stream progress events live and
  survive page refreshes.
- **Never fake a step.** Each answer says where it came from (official docs or the model's own
  knowledge), with confidence, citations and cost.

> Status: hackathon build in progress. Working today:
> - the chat app
> - background jobs and live events
> - uploads and media playback
> - the **docs research agent** (live on Meta)
>
> Next: sandboxed execution on Modal, then step verification and the tutorial video.

---

## Features

| Area | What you get |
| --- | --- |
| Chat UI | ChatGPT-style dashboard with a Markdown renderer that plays inline images, video and audio, image/video/file uploads, a live run panel and finish notifications |
| Auth | Public landing page, login wall (default `admin` / `cueframe`), hashed passwords (scrypt), HTTP-only session cookie or `Authorization: Bearer` token |
| Persistence | SQLite for users, sessions, conversations, messages, uploads, jobs, events and WebSocket connections |
| Background jobs | `@job_handler("kind")` registry with phases, progress, cancellation and crash recovery (`interrupted`); each job links to its conversation, connection and a future `sandbox_id` |
| Realtime | `/api/ws` with persisted connection ids; opening a conversation reports `has_pending_job`, so a refresh re-attaches instead of starting a new job |
| Self-healing tutorials | Failed steps are rewritten from their real output and re-recorded, broken sandboxes replaced, transient ElevenLabs/R2/render errors retried, the finished MP4 checked (size, length, sound, captions, black frames) and interrupted jobs resumed after a restart; every fix is shown in the chat ([docs](docs/tutorial-renderer.md#self-healing)) |
| Docs research agent | Meta `web_search` finds the official docs, we fetch and clean the pages locally, and the model reasons over text and doc images to produce Pydantic-validated steps with confidence. It falls back to model knowledge, clearly labelled |
| Shared infrastructure | One logger (`app/log.py`, job id on every line plus key=value fields), one Meta client (`app/llm.py`: retries, JSON-schema contracts, token and cost accounting) |

## Architecture

```
Browser UI ─┐                         ┌─ Meta Model API (Muse Spark / Wonder, web_search,
Postman ────┼─ REST + /api/ws ─ FastAPI ─┤   computer use, SAM 3.1, Muse Image, ASR)
MCP/scripts ┘        │                │  └─ Modal Sandboxes (planned: virtual desktop per job)
                     │                │
                SQLite store ── EventBus ── JobManager ── handlers/ (assistant.reply, tutorial.make/edit, docs.research, sandbox.run)
```

| Path | Role |
| --- | --- |
| `app/main.py` | App factory and lifespan; wires store, event bus, job manager and the Meta client |
| `app/api.py`, `app/ws.py`, `app/services.py` | REST, WebSocket and the shared service layer they both use |
| `app/db.py`, `app/events.py`, `app/jobs.py` | SQLite store, persisted event fan-out, background job runner |
| `app/llm.py`, `app/log.py`, `app/config.py` | Central Meta client, central logger, settings from env |
| `app/agents/` | `contracts.py` (Pydantic), `docs_research.py` (agent), `web.py` (fetch, clean, BM25) |
| `app/handlers/` | Job kinds: `assistant.reply` (default: routes to a tutorial or a chat answer), `tutorial.make`, `tutorial.edit`, `docs.research`, `sandbox.run`, `demo.reply` |
| `app/tutorial/` | Tutorial pipeline: recipes or automatic planning (`planner.py`: docs research → plan → safety checks → rehearsal), voice, capture, state graph, parallel render ([docs/tutorial-renderer.md](docs/tutorial-renderer.md)) |
| `app/templates/`, `app/static/` | Landing, login and dashboard (Jinja, Tailwind, vanilla JS) |
| `docs/` | Meta tooling research, live test results, token optimization, partner services plan |
| `scripts/` | Live smoke tests and benchmarks against the Meta API |

## Quick start

Requirements: Python 3.12+, [uv](https://docs.astral.sh/uv/), Node 18+ (for Tailwind), and optionally ffmpeg.

```bash
uv sync && npm install && npm run build:css
cp .env.example .env              # set MODEL_API_KEY to enable the Meta agents
uv run uvicorn app.main:app --reload --port 8000
```

Open `http://localhost:8000`, click **Dashboard**, and log in with `admin` / `cueframe`. Interactive
API docs are at `/docs`.

```bash
uv run pytest -q          # tests (Meta and web calls are mocked)
uv run ruff check .       # lint
```

### Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `MODEL_API_KEY` | — | Meta Model API key (dev.meta.ai); enables `docs.research` |
| `META_BASE_URL` | `https://api.meta.ai/v1` | Meta API base URL |
| `CUEFRAME_RESEARCH_MODEL` | `muse-spark-1.3-contributor` | Model for docs research |
| `CUEFRAME_RESEARCH_CONFIDENCE` | `0.7` | Below this, fall back to model knowledge |
| `CUEFRAME_RESEARCH_MAX_PAGES` / `_MAX_ROUNDS` | `4` / `2` | Pages read per round / follow-up rounds |
| `CUEFRAME_ADMIN_USERNAME` / `_PASSWORD` | `admin` / `cueframe` | Seeded login |
| `CUEFRAME_DATA_DIR` | `data` | SQLite database, uploads, artifacts |
| `CUEFRAME_MAX_CONCURRENT_JOBS` | `2` | Job concurrency |
| `CUEFRAME_SANDBOX_PROVIDER` | `modal` | `modal` or `fake` (tests / no Modal) |
| `CUEFRAME_MODAL_PROFILE` | unset | Modal CLI profile to use; overrides saved `MODAL_TOKEN_*` env vars |
| `CUEFRAME_SANDBOX_IDLE_SECONDS` / `CUEFRAME_SANDBOX_MAX_SECONDS` | `120` / `900` | Auto-shutdown limits (also enforced by Modal) |
| `CUEFRAME_SANDBOX_APT_EXTRA` | empty | Extra Debian packages baked into the desktop image (e.g. `gimp`) |
| `CUEFRAME_SANDBOX_USD_PER_HOUR` | `0.11` | Used for the per-sandbox cost estimate |
| `R2_ENDPOINT_URL`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET_NAME` | unset | Store recordings/screenshots in R2 (else `data/artifacts`) |
| `CUEFRAME_R2_PREFIX` | `cueframe` | Key prefix inside the bucket |
| `CUEFRAME_LOG_LEVEL` | `INFO` | Central logger level |

See `.env.example` for the full list.

## Docs research agent

Ask "How do I export a vertical 9:16 video in Shotcut?" and get cited, verified steps.

| Stage | What happens | Cost driver |
| --- | --- | --- |
| discover | Meta `web_search` (`search_context_size: low`, `max_tool_calls: 2`) returns the official domains and URLs | 1–2 searches |
| select | The model picks the best pages from BM25-ranked candidates | small call |
| read | Pages are fetched and cleaned locally into sections, images and links | **0 tokens** |
| assess | The model reads the top excerpts plus up to 2 doc screenshots and returns steps and a confidence, or asks for follow-up links (2 rounds) | about 3k input tokens |
| fallback | If confidence < 0.7, the model answers from its own knowledge, labelled `model_knowledge` | no tools |

Citations and images are kept only if they point to pages or images the agent actually fetched.
Measured live: about 12–14k tokens and **under $0.01 per question**, compared with about 39k tokens
for a naive web-search call. See `docs/token-optimization.md`.

```bash
# synchronous (Postman-friendly)
curl -s localhost:8000/api/research/steps -H "$H" -H 'content-type: application/json' \
  -d '{"query":"How do I export a vertical 9:16 video?","app":"Shotcut"}'
# background job in a conversation, with live progress + Markdown reply
curl -s localhost:8000/api/messages -H "$H" -H 'content-type: application/json' \
  -d '{"content":"How do I export a vertical 9:16 video in Shotcut?","job_kind":"docs.research"}'
```

## API

```bash
TOKEN=$(curl -s localhost:8000/api/auth/login -H 'content-type: application/json' \
  -d '{"username":"admin","password":"cueframe"}' | jq -r .token)
H="Authorization: Bearer $TOKEN"
curl -s localhost:8000/api/uploads -H "$H" -F files=@screenshot.png         # -> [{id, url, kind}]
curl -s localhost:8000/api/messages -H "$H" -H 'content-type: application/json' \
  -d '{"content":"Teach me X","attachment_ids":["u_..."]}'                    # -> {conversation, message, job}
curl -s localhost:8000/api/conversations/c_... -H "$H"                       # -> {..., has_pending_job, active_jobs}
curl -s "localhost:8000/api/events?after=0&conversation_id=c_..." -H "$H"    # poll progress
```

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/api/auth/login` · `/api/auth/logout` · GET `/api/auth/me` | Token auth (also sets the `cf_session` cookie) |
| GET/POST | `/api/conversations` | List / create |
| GET/PATCH/DELETE | `/api/conversations/{cid}` | State including `messages`, `jobs`, `active_jobs`, `has_pending_job`, `connections` |
| POST | `/api/conversations/{cid}/messages` | Send a message, which starts a job (`job_kind`, default `assistant.reply`) |
| POST | `/api/messages` | Same, but creates the conversation if `conversation_id` is omitted |
| POST | `/api/uploads` | Multipart `files`; returns upload ids for `attachment_ids` |
| GET | `/api/jobs` · `/api/jobs/{jid}` · POST `/api/jobs/{jid}/cancel` | Jobs |
| GET | `/api/events?after=SEQ&conversation_id=` | Persisted event log (REST alternative to the socket) |
| GET | `/api/connections` | WebSocket connections and the conversation each has open |
| POST | `/api/research/steps` | Run the docs research agent synchronously |

### WebSocket `/api/ws?client=ui&after=SEQ`

Authenticate with the session cookie, `?token=...`, or `Authorization: Bearer`. Each socket gets a
`connection_id` (`ws_…`) stored in SQLite with the conversation it has open.

```jsonc
// client -> server (request_id optional, echoed back)
{"type":"open","conversation_id":"c_…"}            // -> conversation.state (has_pending_job, active_jobs)
{"type":"message.send","conversation_id":"c_…","content":"…","attachment_ids":[],"job_kind":"docs.research"}
{"type":"job.cancel","job_id":"j_…"}
{"type":"ping"}
// server -> client
hello · event (job.*, phase.changed, job.progress, message.created …) · conversation.state · ack · pong · error
```

Send the optional header `X-Connection-Id: ws_...` on message requests to link the job to a socket.

### Adding a job kind

```python
from app.jobs import JobContext, job_handler


@job_handler("my.kind")
async def run(ctx: JobContext) -> dict:
    ctx.phase("research", "Reading docs")
    ctx.progress(0.3)
    ctx.reply("**Done** ![preview](/media/artifacts/...)")  # Markdown with media
    return {"ok": True}  # stored as the job result
```

## Roadmap

1. **Sandbox (Modal):** a virtual desktop per job, with a live view in the UI, screenshots, ffmpeg recording and command/response.
2. **Do it:** Muse Spark `computer` tool drives the sandbox; every action is logged as an event.
3. **Verify:** Muse Spark video understanding checks each step clip, and SAM 3.1 highlights the clicked UI element.
4. **Deliver:** narrated tutorial video stitched with ffmpeg, Muse Image thumbnail, and Muse Spark Open (Baseten) as an independent verifier.

See `docs/services-plan.md` for the partner service map and `docs/meta-live-test.md` for live results per Meta model.

## Database note

There are no migrations yet. After a schema change, delete `data/` (local dev only).
