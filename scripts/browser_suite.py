"""Run a fixed set of real browser tutorials through the chat API, as a user would, and
report pass rate, time and sandbox minutes per task.

  uv run python scripts/browser_suite.py --base http://localhost:8000 [--only 1,3]

Each task is sent as a chat message; an install/run question is answered "yes, record".
Results go to stdout and to data/browser_suite.json. Uses real Modal sandboxes."""

import argparse
import json
import time
from pathlib import Path

import httpx

TASKS = [
    "Make a tutorial: search the Python docs for asyncio using the search box and open "
    "the asyncio page",
    "Make a tutorial: on Wikipedia, search for Alan Turing and open the Early life section "
    "of his article",
    "Make a tutorial: on MDN Web Docs, search for fetch and open the Fetch API page",
    "Make a tutorial: on OpenStreetMap, search for the Eiffel Tower and show it on the map",
    "Make a tutorial: in Excalidraw, draw a rectangle on the canvas and write Hello inside it",
]


def run(c: httpx.Client, cid: str, text: str, limit: float) -> dict:
    t0 = time.time()
    job = c.post(f"/api/conversations/{cid}/messages", json={"content": text}).json()["job"]
    jid, answered = job["id"], set()
    while time.time() - t0 < limit:
        job = c.get(f"/api/jobs/{jid}").json()
        ask = job.get("pending_input") or {}
        if job["status"] == "waiting_input" and ask.get("input_id") not in answered:
            value = next((o for o in ask.get("options") or [] if "record" in o.lower()), None)
            value = value or (ask.get("options") or ["Yes, run it and record the tutorial"])[0]
            c.post(f"/api/jobs/{jid}/answer", json={"input_id": ask["input_id"], "value": value})
            answered.add(ask["input_id"])
        if job["status"] in ("succeeded", "failed", "cancelled"):
            break
        time.sleep(5)
    else:
        c.post(f"/api/jobs/{jid}/cancel")
        job = c.get(f"/api/jobs/{jid}").json()
    boxes = [b for b in c.get("/api/sandboxes").json() if b.get("job_id") == jid]
    minutes = sum(b.get("seconds") or 0 for b in boxes) / 60
    res = job.get("result") or {}
    return {"task": text, "job": jid, "status": job["status"], "error": job.get("error"),
            "seconds": round(time.time() - t0), "sandboxes": len(boxes),
            "sandbox_minutes": round(minutes, 1), "video": res.get("video_url") or (res.get("video") or {}).get("url"),
            "healing": len(res.get("healing") or [])}  # fmt: skip


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8000")
    ap.add_argument("--user", default="admin")
    ap.add_argument("--password", default="cueframe")
    ap.add_argument("--only", default="", help="1-based task numbers, e.g. 1,3")
    ap.add_argument("--limit", type=float, default=1800, help="seconds per task")
    a = ap.parse_args()
    c = httpx.Client(base_url=a.base, timeout=60)
    tok = c.post("/api/auth/login", json={"username": a.user, "password": a.password})
    c.headers["Authorization"] = f"Bearer {tok.json()['token']}"
    cid = c.post("/api/conversations", json={"title": "Browser suite"}).json()["id"]
    pick = [int(x) - 1 for x in a.only.split(",") if x.strip()] or range(len(TASKS))
    out = []
    for k in pick:
        r = run(c, cid, TASKS[k], a.limit)
        out.append(r)
        print(json.dumps(r), flush=True)
    ok = sum(r["status"] == "succeeded" for r in out)
    print(f"passed {ok}/{len(out)}; sandbox minutes {sum(r['sandbox_minutes'] for r in out):.1f}")
    Path("data/browser_suite.json").write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
