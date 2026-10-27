# Tutorial renderer

Turns a real sandbox run into a narrated 1080p tutorial.

Everything runs as background jobs started from the chat (or the API). No scripts.

1. **Route** (`app/handlers/tutorial.py`, `assistant.reply`, the default chat job): the message is classified (Meta model, or keyword match without a key) as *make a tutorial*, *edit the tutorial in this conversation*, or *chat* (docs research).
2. **Recipe** (`tutorials/<id>/spec.json`, `narration.json`, loaded by `app/tutorial/recipes.py`): the verified steps (sandbox `Action`s with `app` = `terminal` | `browser`), labels, highlights, badges and narration lines.
3. **Intake** (`tutorial.make`): before any terminal command the job asks "already installed?" in the chat and waits (`waiting_input`; answer from the UI, `POST /api/jobs/{id}/answer` or WS `job.answer`). No sandbox exists while it waits. "Already installed" keeps the step in the run but out of the video.
4. **Voice** (`app/tutorial/voice.py`): ElevenLabs TTS with timestamps (word timings drive captions). The shared SFX/music library is generated once and kept in storage under `assets/`.
5. **Capture** (`app/tutorial/pipeline.py`): one Modal desktop at a fixed 1920×1080, one continuous recording, each step held as long as its narration needs and saved as a `t_in`/`t_out` window. Every app fills the screen with no decorations (Fluxbox `apps`), so only the focused app is visible. The sandbox is terminated as soon as the run ends (and the Modal app stopped when no sandbox is left).
6. **State graph** (`app/tutorial/contracts.py`, `build.py`): one `State` per step with source window, narration, captions, camera keyframes, highlights, SFX and verification. A failed step ends the video and is marked failed, never faked.
7. **Render** (`app/tutorial/render.py`, `renderer/`): Remotion renders every state as its own segment, in parallel. A segment is keyed by a hash of its state JSON plus the renderer code and cached in storage, so an edit re-renders only the states that changed. Segments are joined with stream copy; audio is mixed separately (sidechain-ducked music, `loudnorm` to -14 LUFS) and muxed as AAC with `+faststart`.
8. **Publish**: video, captions (.srt), thumbnail and YouTube text (`youtube-vN.md`: title, description, chapters, commands, tags) go to R2 (local artifacts dir without R2) and are posted in the chat. Media URLs are only served to the tutorial's owner.

## Storage

SQLite table `tutorials`: recipe with edits (`spec`), voice lines with word timings and storage refs (`voice`), capture records (`capture`), the state graph (`graph`), output refs and render stats (`output`), `version`, `status`. Binary media live only in storage, under `tutorials/<tutorial id>/`. `data/tutorials/<id>/` is a cache that is refilled from storage when needed.

## Editing

- Chat: "make the intro shorter", "zoom in more on the curl output". The model turns it into `StateEdit`s (narration, label, zoom, highlight) on the existing graph. Only changed lines are re-voiced and only changed segments are re-rendered, then a new version is posted. Recorded actions cannot be changed by an edit.
- API: `PATCH /api/tutorials/{id}/states/{state_id}` with `{"narration": [...], "label", "zoom", "highlight"}`, or `POST /api/tutorials/{id}/render`. Both return the background job. `GET /api/tutorials[?conversation_id=]` and `GET /api/tutorials/{id}` return the graph.

Not yet done: planning recipes automatically for any task (currently: `nginx-website`).

## Automatic planning

Requests that match no hand-written recipe go through `app/tutorial/planner.py`:

1. `DocsResearchAgent` reads the official docs (best effort).
2. The research model returns a structured `TutorialPlan`: title, intro/outro, 3-7 steps of kind
   `terminal` (one non-interactive line), `open_url` or `key`, each with narration, label, badge,
   highlight, `expect` text and an `installs` flag. `say` maps written names to spoken forms
   (`jq` → `J Q`); on-screen text always uses the written form.
3. `problems()` rejects unsafe or malformed plans (destructive or interactive commands, multi-line
   commands, missing URLs, too little narration) and asks the model again.
4. Intake: the chat shows the commands and asks before installs (or once, if nothing installs).
   No sandbox exists until the user answers.
5. Rehearsal: every step runs in a private, unrecorded sandbox. `check()` requires exit 0, the
   `expect` text in the terminal output and HTTP 2xx/3xx for URLs. A failed step is rewritten from
   its real output, up to `MAX_REPAIRS` times; otherwise the job fails and nothing is recorded.
6. The proven plan is recorded in a fresh 1080p sandbox with the normal capture → voice → render →
   R2 path; the same checks run again.

## Self-healing

Every tutorial job corrects itself inside the normal pipeline; each recovery is shown live as a
"Healing" note in the chat, emitted as a `tutorial.healing` event, stored in `spec.healing` and
`output.healing`, and listed in the final reply. The video only ever shows what really happened.

| Problem | What the system does (`app/heal.py`, `pipeline.py`, `planner.py`, `qa.py`) |
| --- | --- |
| A step fails in rehearsal | Rewrites that step from its real output, up to `MAX_REPAIRS` (3) times |
| A rehearsal step can't be fixed | Plans the whole tutorial again once with the failure as context, asks about any new install, rehearses again; fails honestly if that also fails |
| A step fails while recording | Rewrites the step from its real output, re-voices only its lines, records again in a fresh sandbox (`MAX_TAKES` = 3); the last take is published as an honest failure |
| A sandbox breaks (rehearsal or recording) | Starts a fresh sandbox and runs again (sandboxes always stop in `finally`) |
| ElevenLabs, R2 upload or Remotion segment errors | `retry()` with exponential backoff; only transient errors (timeouts, connection errors, HTTP 408/409/425/429/5xx, Modal/gRPC) are retried, a segment of the wrong length is rendered again |
| Raw recording is missing, too short or blank inside a step | `qa.recording()` rejects the take and records again |
| Final MP4 is not 1920×1080, the wrong length, silent or has empty captions | `qa.final()` triggers one full re-render; if it still fails the tutorial is marked failed with the reasons |
| Server restarts mid-job | Interrupted tutorial jobs are resumed (`tutorial.resume`): if the recording is saved it only renders, otherwise it narrates and records again; earlier intake answers are kept |
