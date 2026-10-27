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
