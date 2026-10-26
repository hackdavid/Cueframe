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
