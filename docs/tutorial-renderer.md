# Tutorial renderer

Turns a real sandbox run into a narrated 1080p tutorial.

1. **Spec** (`tutorials/<id>/spec.json`, `narration.json`): the steps (sandbox `Action`s with `app` = `terminal` | `browser`), labels, highlights, badges and the narration lines.
2. **Voice** (`app/tutorial/voice.py`): ElevenLabs TTS with timestamps (word timings drive captions) and ElevenLabs sound generation for click / keys / whoosh / success / pop / music.
3. **Capture** (`scripts/capture_tutorial.py`): one Modal desktop at a fixed 1920×1080, one continuous recording; each step is held long enough for its narration and saved as a `t_in`/`t_out` window in `capture.json`. Every app fills the screen with no decorations (Fluxbox `apps`), so only the focused app is visible.
4. **State graph** (`app/tutorial/contracts.py`, `build.py`): one `State` per step with source window, narration, captions, camera keyframes, highlights, SFX and verification badge.
5. **Render** (`scripts/render_tutorial.py`, `renderer/`): Remotion renders every state as its own segment, in parallel. A segment is keyed by a hash of its state JSON plus the renderer code, so an edit re-renders only the states that changed. Segments are joined with stream copy; audio is mixed separately (sidechain-ducked music, `loudnorm` to -14 LUFS) and muxed as AAC with `+faststart`.

```bash
cd renderer && npm install && cd ..
ELEVENLABS_API_KEY=... ELEVENLABS_VOICE_ID=... PYTHONPATH=. uv run python scripts/capture_tutorial.py tutorials/nginx-website build/nginx-website
PYTHONPATH=. uv run python scripts/render_tutorial.py tutorials/nginx-website build/nginx-website
```

Output: `build/<id>/render/<id>.mp4` (1920×1080, 30 fps, H.264 + AAC) and `<id>.srt`.

Not yet done: storing the graph in SQLite and segments in R2, chat-driven state edits, and generating specs automatically.
