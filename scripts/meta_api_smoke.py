import asyncio
import base64
import json
import os
import time

import httpx

K = os.environ["MODEL_API_KEY"]
B = "https://api.meta.ai/v1"
H = {"Authorization": f"Bearer {K}"}
b64 = lambda p: base64.b64encode(open(p, "rb").read()).decode()


def show(name, r, t, extra=""):
    open(f"out2_{name}.txt", "w").write(r.text if hasattr(r, "text") else str(r))
    print(
        f"## {name} {getattr(r, 'status_code', '')} {time.time() - t:.1f}s {extra}\n",
        (r.text if hasattr(r, "text") else str(r))[:600].replace("\n", " "),
        "\n",
        flush=True,
    )


async def text_of(d):
    return " ".join(
        c.get("text", "")
        for o in d.get("output", [])
        if o["type"] == "message"
        for c in o["content"]
    )


async def vision(c):
    t = time.time()
    r = await c.post(
        B + "/responses",
        json={
            "model": "muse-spark-1.3",
            "input": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": "This is a screenshot of a web app step. In one sentence: what page is this, and is a username typed into the username field? Answer JSON {page, username_filled:true/false, evidence}.",
                        },
                        {
                            "type": "input_image",
                            "image_url": "data:image/png;base64," + b64("login2.png"),
                        },
                    ],
                }
            ],
        },
    )
    show("vision", r, t, (await text_of(r.json()))[:300] if r.status_code == 200 else "")


async def video(c):
    t = time.time()
    u = await c.post(
        B + "/files",
        files={"file": ("clip.mp4", open("clip.mp4", "rb"), "video/mp4")},
        data={"purpose": "user_data"},
    )
    show("file_upload", u, t)
    if u.status_code != 200:
        return
    fid = u.json()["id"]
    t = time.time()
    r = await c.post(
        B + "/responses",
        json={
            "model": "muse-spark-1.3",
            "input": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": "This clip records a tutorial step. List what happens in order with timestamps, then say whether the step 'type admin into the username field' visibly succeeded.",
                        },
                        {"type": "input_file", "file_id": fid},
                    ],
                }
            ],
        },
    )
    show(
        "video_understanding", r, t, (await text_of(r.json()))[:500] if r.status_code == 200 else ""
    )
    t = time.time()
    r = await c.post(
        B + "/responses",
        headers={"Accept": "text/event-stream"},
        json={
            "model": "sam-3.1",
            "stream": True,
            "input": [
                {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": "username input field"},
                        {"type": "input_video", "file_id": fid},
                    ],
                }
            ],
        },
    )
    lines = [l for l in r.text.splitlines() if "output_text.delta" in l or '"delta"' in l]
    show(
        "sam_video", r, t, f"delta_lines={len(lines)} first={lines[:1][0][:200] if lines else None}"
    )


async def sam_img(c):
    t = time.time()
    r = await c.post(
        B + "/responses",
        headers={"Accept": "text/event-stream"},
        json={
            "model": "sam-3.1",
            "stream": True,
            "input": [
                {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": "button"},
                        {
                            "type": "input_image",
                            "image_url": "data:image/png;base64," + b64("login.png"),
                        },
                    ],
                }
            ],
        },
    )
    d = [
        json.loads(l[5:]).get("delta", "")
        for l in r.text.splitlines()
        if l.startswith("data:") and "output_text.delta" in l
    ]
    show("sam_image", r, t, "DELTAS: " + " | ".join(x[:160] for x in d[:3]))


async def asr(c):
    t = time.time()
    r = await c.post(
        B + "/asr/transcribe",
        headers={"Accept": "application/json"},
        files={
            "request": (
                None,
                json.dumps(
                    {
                        "model": "muse-voice-transcribe-1.0",
                        "audioEncoding": "WAV",
                        "mode": "PUSH_TO_TALK",
                    }
                ),
                "application/json",
            ),
            "audio": ("jfk.wav", open("jfk.wav", "rb"), "audio/wav"),
        },
    )
    show("asr", r, t)


async def image(c):
    t = time.time()
    r = await c.post(
        B + "/images/generations",
        json={
            "model": "muse-image-1.0",
            "prompt": "Clean YouTube thumbnail: a friendly film director clapperboard over a laptop showing a video editor timeline, bold text 'Cueframe', dark navy and amber",
            "n": 1,
        },
    )
    ex = ""
    if r.status_code == 200:
        d = r.json()["data"][0]
        if d.get("b64_json"):
            open("thumb.png", "wb").write(base64.b64decode(d["b64_json"]))
            ex = "saved thumb.png"
        else:
            ex = "url " + str(d.get("url"))[:120]
    show("image_gen", r, t, ex)


async def messages(c):
    t = time.time()
    r = await c.post(
        B + "/messages",
        headers={"anthropic-version": "2023-06-01", "x-api-key": K},
        json={
            "model": "muse-spark-1.3",
            "max_tokens": 200,
            "messages": [{"role": "user", "content": "Reply with exactly: pong"}],
        },
    )
    show("anthropic_messages", r, t)


async def main():
    async with httpx.AsyncClient(headers=H, timeout=300) as c:
        await asyncio.gather(
            vision(c), video(c), sam_img(c), asr(c), image(c), messages(c), return_exceptions=False
        )


asyncio.run(main())
