# Sandbox plan (Modal)

Status: designed, with a prototype in `scripts/modal_desktop_probe.py`. The prototype has not run
yet because Modal rejected the launch with `Workspace … has exceeded its spend limit`.

## One sandbox per job

`modal.Sandbox.create(...)` uses a prebuilt image containing:
- Xvfb (virtual display `:99`) and the fluxbox window manager
- Chromium and the target app (for example Shotcut)
- xdotool (input) and ffmpeg (capture)
- x11vnc and websockify/noVNC (live view)

Each sandbox gets 2 CPUs and 4 GB of RAM, a hard `timeout`, and an `encrypted_ports` tunnel. The
job stores `sandbox_id` and the tunnel URL, so a page refresh re-attaches to the same desktop.

| Need | How |
| --- | --- |
| Send a command | `sb.exec("bash", "-c", "DISPLAY=:99 xdotool click 1")` returns stdout, stderr and the exit code. This is what the prototype uses |
| Faster two-way channel | A small in-sandbox agent: a WebSocket server behind a second tunnel port. Cueframe sends `click`, `type`, `key`, `screenshot` or `exec`; the sandbox streams back results, logs and frames |
| Screenshot | `ffmpeg -f x11grab -i :99 -frames:v 1 shot.png`. These frames feed the Muse Spark `computer` loop and the run panel |
| Recording | `ffmpeg -f x11grab -framerate 15 … step-N.mp4`, one file per step, stopped with SIGINT. Files are copied out into job artifacts for verification and the final tutorial |
| Live view | noVNC served on port 6080 through the Modal tunnel (`sb.tunnels()[6080].url + "/vnc.html?view_only=1"`) and embedded as an iframe in the run panel |
| Cleanup | `sb.terminate()` when the job finishes or is cancelled; the timeout is the backstop |

## Loop per step

1. Take a screenshot.
2. Muse Spark (`computer` tool) returns actions.
3. Execute them with xdotool.
4. Record the step clip.
5. Verify the step (Muse Spark video, SAM 3.1).
6. Emit events to the chat.

## Phase 1 implementation (`sandbox.run`)

| Piece | Where |
|---|---|
| Provider interface + action scripts | `app/sandbox/base.py`, `app/sandbox/bin/*.sh` |
| Modal provider (desktop image, live view, file transfer) | `app/sandbox/modal_box.py` |
| Deterministic test provider | `app/sandbox/fake.py` (`CUEFRAME_SANDBOX_PROVIDER=fake`) |
| Lifecycle + shutdown guarantees | `app/sandbox/manager.py`, table `sandboxes` |
| Artifact storage (R2, else local) | `app/storage.py`; DB stores only keys/URLs |
| Job handler | `app/handlers/sandbox_run.py` |

**Shutdown rules.** A sandbox is terminated when its job succeeds, fails or is cancelled
(`SandboxManager.session` `finally`), after `CUEFRAME_SANDBOX_IDLE_SECONDS` with no commands,
after `CUEFRAME_SANDBOX_MAX_SECONDS` (also passed to Modal as `timeout`/`idle_timeout`, so
Modal kills it even if Cueframe crashes), on server start (leftovers reaped) and on server stop.
Each row records `seconds` and an `est_cost_usd` from `CUEFRAME_SANDBOX_USD_PER_HOUR`.

**User intake.** Every `terminal` action with `ask: true` puts the job in `waiting_input`
*before* the sandbox starts (no paid idle time). Answer with `POST /api/jobs/{id}/answer`
`{"input_id", "value"}` or the WS frame `{"type": "job.answer", "job_id", "input_id", "value"}`.
"Already installed" still runs the step (later steps need it) but marks it `in_video: false`.

**Recording.** One continuous 30 fps fixed-size recording per job (fragmented MP4, keyframe
every second). Each action is timed on the sandbox clock (`started`, `ended`, `settled` = screen
stopped changing) and mapped to `t_in`/`t_out` offsets into that recording; idle gaps between
actions are never part of any state window.

**Long commands.** Terminal commands are typed into a visible xterm. Completion is detected from
the shell prompt hook (`/tmp/cf/prompt_n`), not by blocking; output lines stream as
`sandbox.output` events. A per-action `timeout` marks the step failed (exit 124) instead of hanging.

**API.** `GET /api/sandboxes`, `POST /api/sandboxes/{id}/stop`,
`POST /api/jobs/{id}/sandbox/actions` (one `Action`, returns the timed `ActionRecord`),
`GET /api/jobs/{id}/sandbox/screenshot`. Start a run with `POST /api/messages`
`{"job_kind": "sandbox.run", "job_input": {"start_url": ..., "actions": [...]}}`.
