"""Drive a real end-to-end video job through the local server: log in, post a GitHub
repo request, answer the planner's questions, wait for the video, print the result."""

import json
import sys
import time
from pathlib import Path

import httpx

BASE = "http://localhost:8001"
GOAL = ("Make a 60 second reel about the GitHub repo https://github.com/astral-sh/uv "
        "- show the repo page, its stars, how active it is, and who uses it.")


def answer_for(question: str, options: list[str]) -> str:
    q = question.lower()
    if "kind of video" in q:
        return next(o for o in options if "reel" in o.lower())
    if "how long" in q:
        return next(o for o in options if "60" in o)
    if "already installed" in q:
        return next(o for o in options if o.startswith("Not installed"))
    if "shall i go ahead" in q or "run it" in q:
        return next(o for o in options if o.startswith("Yes"))
    return options[0]


def main() -> None:
    env = dict(l.split("=", 1) for l in open(".env", encoding="utf-8")
               if "=" in l and not l.startswith("#"))
    c = httpx.Client(base_url=BASE, timeout=30)
    r = c.post("/api/auth/login", json={
        "username": env["CUEFRAME_ADMIN_USERNAME"].strip(),
        "password": env["CUEFRAME_ADMIN_PASSWORD"].strip()})
    r.raise_for_status()
    c.headers["Authorization"] = f"Bearer {r.json()['token']}"
    print("logged in", flush=True)

    r = c.post("/api/messages", json={"content": GOAL})
    r.raise_for_status()
    body = r.json()
    jid = body["job"]["id"]
    print("job", jid, flush=True)
    watch(c, jid)


def watch(c: httpx.Client, jid: str) -> None:
    t0 = time.time()
    last = ""
    job: dict = {}
    while time.time() - t0 < 2700:  # 45 min cap
        try:
            job = c.get(f"/api/jobs/{jid}").json()
        except httpx.HTTPError as exc:  # a dropped poll doesn't end the watch
            print(f"  (poll failed: {exc}; retrying)", flush=True)
            time.sleep(10)
            continue
        status = job["status"]
        line = f"{time.time() - t0:6.0f}s  {status}  {job.get('phase') or ''}  {job.get('progress') or ''}"
        if line[8:] != last:
            print(line, flush=True)
            last = line[8:]
        if status == "waiting_input":
            pi = job["pending_input"]
            value = answer_for(pi["question"], pi.get("options") or [])
            print(f"  -> answering {pi['question']!r} with {value!r}", flush=True)
            c.post(f"/api/jobs/{jid}/answer",
                   json={"input_id": pi["input_id"], "value": value}).raise_for_status()
        elif status in ("succeeded", "failed", "cancelled"):
            break
        time.sleep(5)
    print("final:", job["status"], job.get("error") or "", flush=True)
    result = job.get("result") or {}
    tid = result.get("tutorial_id")
    if not tid:
        print(json.dumps(result, indent=2)[:2000], flush=True)
        return
    tut = c.get(f"/api/tutorials/{tid}").json()
    out = {
        "title": tut["spec"]["title"],
        "video": (tut.get("output") or {}).get("video", {}).get("url"),
        "qa": (tut.get("output") or {}).get("qa"),
        "healing": (tut.get("output") or {}).get("healing"),
        "facts": {k: v["value"] for k, v in (tut["spec"].get("facts") or {}).get("items", {}).items()},
        "steps": [{"id": s["id"], "scene": s.get("scene"),
                   "overlay": (s.get("overlay") or {}).get("component"),
                   "board": s.get("board_topic")} for s in tut["spec"]["steps"]],
        "states": [{"id": s["id"], "kind": s["kind"], "duration": s["duration"],
                    "has_source": bool(s.get("source")),
                    "media_url": s.get("media_url"),
                    "overlays": [o["component"] for o in s.get("overlays") or []],
                    "highlights": [{"kind": h.get("kind"), "cursor": h.get("cursor"),
                                    "dim": h.get("dim")} for h in s.get("highlights") or []]}
                   for s in tut["graph"]["states"]],
        "lines": list(tut["spec"]["lines"].values()),
    }
    Path("data/e2e_result.json").write_text(json.dumps(out, indent=2))
    print(json.dumps(out, indent=2), flush=True)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "watch":
        env = dict(l.split("=", 1) for l in open(".env", encoding="utf-8")
                   if "=" in l and not l.startswith("#"))
        c = httpx.Client(base_url=BASE, timeout=30)
        r = c.post("/api/auth/login", json={
            "username": env["CUEFRAME_ADMIN_USERNAME"].strip(),
            "password": env["CUEFRAME_ADMIN_PASSWORD"].strip()})
        r.raise_for_status()
        c.headers["Authorization"] = f"Bearer {r.json()['token']}"
        watch(c, sys.argv[2])
    else:
        sys.exit(main())
