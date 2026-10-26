# Cueframe: inference and infrastructure plan (Meta + Baseten + Modal + Fireworks)

Researched 2026-10-26 from dev.meta.ai/docs, docs.baseten.co, modal.com/docs and docs.fireworks.ai.

Problem statement: **Build for Your World**. Learning any software usually means generic,
out-of-date tutorials. Cueframe performs your exact task on your own files, verifies every step,
and hands you the video.

## Pipeline: which service does what

| Stage | Service | Model / product | Why |
| --- | --- | --- | --- |
| Voice goal and transcripts of uploaded media | Meta Model API | Muse Voice Transcribe | Speaker-attributed speech-to-text |
| Docs-first research | Meta Model API | `muse-spark-1.3` + built-in `web_search` | Cited sources shown in the run panel |
| Plan and act in the app | Meta Model API | `muse-wonder-1.0` + `computer` tool | Long-horizon agent; returns click/type actions |
| Sandbox the agent drives | **Modal** | Sandboxes (`modal.Sandbox.create`, `exec`, tunnels, snapshots) | One isolated Linux desktop per job; snapshot a warm image with the target app installed |
| Per-step verification (high volume) | **Baseten** | `meta-models/muse-spark-open-1026` (OpenAI-compatible `https://inference.baseten.co/v1`; vision, audio, `reasoning_effort`) | Checks each step's frames/clip and marks it verified or failed; the 2M TPM limit allows parallel checks |
| Final whole-clip review | Meta Model API | `muse-spark-1.3` video understanding (Files API) | Watches the stitched clip end to end |
| Highlight the clicked UI | Meta Model API | `sam-3.1` (boxes and masks, tracked across frames) | Automatic zoom/highlight overlays |
| Step captions and narration script | **Fireworks** | `muse-glimmer-30b` fine-tuned with LoRA (free SFT until 11/30) on our own verified traces | A small, fast, specialised "Cueframe-Glimmer" model |
| Self-hosted fallback / offline | **Modal** | Muse Glimmer 30B on a Dedicated Endpoint or vLLM (bf16 needs about 72 GB VRAM, so 1×H100 80GB; quantized builds are 17–20 GB) | Shows open weights running on our own GPUs |
| Narration voice | **Modal** GPU (open TTS) or ElevenLabs | Not a Meta model: Meta has no TTS | Needed for the narrated video |
| Stitch and render | **Modal** CPU function | ffmpeg | Runs off the web server |
| Thumbnail | Meta Model API | Muse Image ($0.01 per image) | Cover card |

The FastAPI app (jobs, events, WebSocket, SQLite) stays the orchestrator. Each provider sits behind
its own adapter with a labelled mock, so the app runs before every key arrives.

## Facts confirmed in the docs

- **Meta Model API:** `https://api.meta.ai/v1`, Bearer `MODEL_API_KEY`, OpenAI and Anthropic
  compatible. The OpenAI Agents SDK should work unchanged (it uses the same `web_search` and
  `computer` tool types). No TTS, code interpreter or hosted MCP.
- **Baseten:** `https://inference.baseten.co/v1/chat/completions`, `BASETEN_API_KEY`. The hackathon
  model id is `meta-models/muse-spark-open-1026`.
  - Audio input: WAV/MP3/FLAC/OGG/M4A, up to 4 files, each ≤25 MB and ≤5 minutes.
  - Image input is supported. Reasoning comes back as separate output.
- **Muse Spark Open:** an 816B-A42B MoE needing 4–8×B300, so self-hosting it is not realistic;
  calling it through Baseten is the right choice.
- **Modal:** Sandboxes for untrusted agent execution; Endpoints (Shared, or Dedicated with custom
  weights, behind an OpenAI-compatible API); serverless GPUs. Earlier sessions hit a workspace
  spend-limit gate, so check the budget before the demo.
- **Fireworks:** `accounts/fireworks/models/muse-glimmer-30b` is the base model for LoRA SFT
  (free via the UI or the Serverless Training API until 11/30). The tuned model is deployed under
  our account.

## Build phases (one PR per phase)

1. Provider adapters (Meta, Baseten, Fireworks, Modal) with mocks, settings, and a `/api/health/providers` check.
2. Modal sandbox runner plus a screenshot/recording loop, driven by Muse Wonder's `computer` tool.
3. Verification: Baseten Muse Spark Open checks each step; Muse Spark reviews the final clip; honest failed states.
4. SAM 3.1 highlights, TTS narration, ffmpeg stitching, and a Muse Image thumbnail.
5. Fine-tune Glimmer on Fireworks from collected traces; Glimmer on Modal as the fallback.
6. Demo polish, a cost-per-tutorial readout, and the recorded demo video.
