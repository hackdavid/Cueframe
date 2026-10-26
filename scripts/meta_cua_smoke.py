import asyncio
import base64
import json
import os
import time

import httpx
from playwright.async_api import async_playwright

K = os.environ["MODEL_API_KEY"]
B = "https://api.meta.ai/v1"
KEYMAP = {
    "CTRL": "Control",
    "CONTROL": "Control",
    "ALT": "Alt",
    "SHIFT": "Shift",
    "ENTER": "Enter",
    "RETURN": "Enter",
    "TAB": "Tab",
    "ESC": "Escape",
    "ESCAPE": "Escape",
    "BACKSPACE": "Backspace",
    "CMD": "Meta",
    "META": "Meta",
    "SPACE": " ",
}


async def run(pg, a):
    t = a["type"]
    m = pg.mouse
    if t == "click":
        await m.click(
            a["x"],
            a["y"],
            button={"wheel": "middle"}.get(a.get("button", "left"), a.get("button", "left"))
            if a.get("button", "left") in ("left", "right", "wheel")
            else "left",
        )
    elif t == "double_click":
        await m.dblclick(a["x"], a["y"])
    elif t == "type":
        await pg.keyboard.type(a["text"])
    elif t == "keypress":
        await pg.keyboard.press("+".join(KEYMAP.get(k.upper(), k) for k in a["keys"]))
    elif t == "move":
        await m.move(a["x"], a["y"])
    elif t == "scroll":
        await m.move(a["x"], a["y"])
        await m.wheel(a.get("scroll_x", 0), a.get("scroll_y", 0))
    elif t == "drag":
        p = a["path"]
        await m.move(p[0]["x"], p[0]["y"])
        await m.down()
        for q in p[1:]:
            await m.move(q["x"], q["y"])
        await m.up()
    elif t in ("wait", "screenshot"):
        await pg.wait_for_timeout(800)


async def main():
    async with (
        async_playwright() as p,
        httpx.AsyncClient(headers={"Authorization": f"Bearer {K}"}, timeout=300) as c,
    ):
        b = await p.chromium.connect_over_cdp("http://localhost:29229")
        ctx = await b.new_context(viewport={"width": 1280, "height": 800})
        pg = await ctx.new_page()
        await pg.goto("http://localhost:8000/login")
        await pg.wait_for_timeout(800)
        shot = lambda: pg.screenshot()
        img = base64.b64encode(await shot()).decode()
        body = {
            "model": "muse-spark-1.3",
            "tools": [{"type": "computer"}],
            "input": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": "You are operating a browser showing a web app. Log in with username 'admin' and password 'cueframe'. Stop and say DONE when the dashboard is visible.",
                        },
                        {"type": "input_image", "image_url": "data:image/png;base64," + img},
                    ],
                }
            ],
        }
        prev = None
        for step in range(1, 11):
            if prev:
                body = {
                    "model": "muse-spark-1.3",
                    "tools": [{"type": "computer"}],
                    "previous_response_id": prev,
                    "input": [out],
                }
            t = time.time()
            r = await c.post(B + "/responses", json=body)
            if r.status_code != 200:
                print("STEP", step, "HTTP", r.status_code, r.text[:500])
                break
            d = r.json()
            prev = d["id"]
            u = d.get("usage", {})
            calls = [o for o in d["output"] if o["type"] == "computer_call"]
            msgs = [
                c2.get("text", "")
                for o in d["output"]
                if o["type"] == "message"
                for c2 in o["content"]
            ]
            print(
                f"STEP {step} {time.time() - t:.1f}s tokens in/out={u.get('input_tokens')}/{u.get('output_tokens')} msg={' '.join(msgs)[:200]!r}",
                flush=True,
            )
            if not calls:
                break
            cc = calls[0]
            print(
                "   actions:",
                json.dumps(cc.get("actions"))[:400],
                "safety:",
                cc.get("pending_safety_checks"),
                flush=True,
            )
            for a in cc.get("actions", []):
                await run(pg, a)
            await pg.wait_for_timeout(1000)
            out = {
                "type": "computer_call_output",
                "call_id": cc["call_id"],
                "acknowledged_safety_checks": cc.get("pending_safety_checks", []),
                "output": {
                    "type": "computer_screenshot",
                    "image_url": "data:image/png;base64," + base64.b64encode(await shot()).decode(),
                },
            }
        print("FINAL URL:", pg.url)
        await pg.screenshot(path="cua_final.png")
        await ctx.close()


asyncio.run(main())
