"""Prototype: Modal CPU sandbox with a virtual desktop, live noVNC view, screenshots,
ffmpeg recording and xdotool commands. Touch ./STOP to terminate."""

import base64, os, time, httpx, modal

W, H = 1280, 720
START = f"""
Xvfb :99 -screen 0 {W}x{H}x24 -nolisten tcp & sleep 1
export DISPLAY=:99
fluxbox >/dev/null 2>&1 &
x11vnc -display :99 -forever -shared -nopw -quiet -rfbport 5900 >/dev/null 2>&1 &
websockify --web /usr/share/novnc 6080 localhost:5900 >/dev/null 2>&1 &
chromium --no-sandbox --disable-gpu --disable-dev-shm-usage --no-first-run --no-default-browser-check \
  --window-size={W},{H} --window-position=0,0 https://www.shotcut.org/ >/dev/null 2>&1 &
wait
"""
t0 = time.time()


def ts(msg):
    print(f"[{time.time() - t0:6.1f}s] {msg}", flush=True)


app = modal.App.lookup("cueframe-sandbox", create_if_missing=True)
image = modal.Image.debian_slim(python_version="3.12").apt_install(
    "xvfb",
    "x11vnc",
    "novnc",
    "websockify",
    "fluxbox",
    "xdotool",
    "ffmpeg",
    "chromium",
    "fonts-dejavu-core",
    "x11-utils",
)
sb = modal.Sandbox.create(
    "bash",
    "-c",
    START,
    app=app,
    image=image,
    cpu=2,
    memory=4096,
    timeout=1200,
    encrypted_ports=[6080],
)
ts(f"sandbox {sb.object_id} created")


def sh(cmd, timeout=60):
    t = time.time()
    p = sb.exec("bash", "-c", cmd, timeout=timeout)
    out = p.stdout.read()
    err = p.stderr.read()
    p.wait()
    return p.returncode, out, err, round(time.time() - t, 2)


for _ in range(60):
    rc, *_ = sh("DISPLAY=:99 xdotool search --onlyvisible --class chromium | head -1 | grep -q .")
    if rc == 0:
        break
    time.sleep(1)
ts("chromium window visible")
url = sb.tunnels()[6080].url
live = url + "/vnc.html?autoconnect=1&resize=scale&view_only=1"
ts(f"LIVE {live}")
r = httpx.get(url + "/vnc.html", timeout=30)
ts(f"noVNC page HTTP {r.status_code}")


def shot(name):
    rc, out, err, dt = sh(
        f"ffmpeg -y -loglevel error -f x11grab -video_size {W}x{H} -i :99 -frames:v 1 /tmp/{name}.png && base64 -w0 /tmp/{name}.png"
    )
    open(f"./{name}.png", "wb").write(base64.b64decode(out))
    ts(f"screenshot {name} rc={rc} {dt}s {err[:100]}")


time.sleep(4)
shot("shot1")
rec = sb.exec(
    "bash",
    "-c",
    f"ffmpeg -y -loglevel error -f x11grab -framerate 15 -video_size {W}x{H} -i :99 -c:v libx264 -preset veryfast -pix_fmt yuv420p -movflags +faststart /tmp/rec.mp4",
)
ts("recording started")
time.sleep(2)
for cmd in [
    "xdotool key ctrl+l",
    "xdotool type --delay 60 'shotcut.org/howtos/'",
    "xdotool key Return",
]:
    rc, out, err, dt = sh("DISPLAY=:99 " + cmd)
    ts(f"cmd {cmd!r} rc={rc} roundtrip={dt}s")
time.sleep(6)
shot("shot2")
rc, out, err, dt = sh("DISPLAY=:99 xdotool getactivewindow getwindowname")
ts(f"active window: {out.strip()!r}")
sh("pkill -INT -f 'x11grab -framerate'")
rec.wait()
ts(f"recording stopped rc={rec.returncode}")
rc, out, err, dt = sh("ls -la /tmp/rec.mp4 && base64 -w0 /tmp/rec.mp4", timeout=120)
data = out.split("\n", 1)[1] if "\n" in out else ""
open("./rec.mp4", "wb").write(base64.b64decode(data))
ts(f"recording downloaded {out.splitlines()[0] if out else err}")
ts("waiting for STOP file (live view stays up)")
while not os.path.exists("./STOP") and time.time() - t0 < 900:
    time.sleep(2)
sb.terminate()
ts("terminated")
