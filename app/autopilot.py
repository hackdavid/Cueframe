"""Daily trending autopilot: at `CUEFRAME_AUTOPILOT_AT` (UTC) it asks the chat for an
influencer video about today's top trending GitHub repo and AI paper, exactly as a user
would (one conversation each, questions answered with their first option). Finished
videos get a caption and hashtags and wait in /autopilot for review. Nothing about the
software or past plans is kept: only which item each day covered, so it isn't repeated."""

import asyncio
import logging
import re
from datetime import UTC, datetime, time
from typing import Any

from app import services

log = logging.getLogger("cueframe.autopilot")

TOPICS = {
    "repo": "Make an influencer video about today's top trending GitHub repo",
    "paper": "Make an influencer video about today's top trending AI paper",
}
BASE_TAGS = {"repo": ["#GitHub", "#OpenSource"], "paper": ["#AI", "#MachineLearning"]}
MAX_TAGS = 10
SKIP_LAST = 14
ACTIVE = ("queued", "running")
ITEM = re.compile(r"^Trending item \(([^)]*)\): (.+?) \[\w+\]$", re.M)
FIELD = r"^{}: (.+)$"


def at_time(value: str) -> time | None:
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", value.strip())
    return time(int(m[1]), int(m[2])) if m and int(m[1]) < 24 and int(m[2]) < 60 else None


def request_for(store: Any, user_id: str, topic: str) -> str:
    seen = [r["item"] for r in store.list_autopilot(user_id) if r["topic"] == topic and r["item"]]
    text = TOPICS[topic]
    if seen:
        text += ". Skip these, they were covered already: " + "; ".join(seen[:SKIP_LAST])
    return text


def hashtag(tag: str) -> str:
    return "#" + "".join(w[:1].upper() + w[1:] for w in re.findall(r"[0-9A-Za-z]+", tag))


def social(spec: dict[str, Any], topic: str) -> dict[str, Any]:
    """Caption and hashtags from the finished plan and its trend brief."""
    brief = spec.get("brief", "")

    def field(name: str) -> str:
        m = re.search(FIELD.format(re.escape(name)), brief, re.M)
        return m[1].strip() if m else ""

    m = ITEM.search(brief)
    item = m[2].strip() if m else spec.get("software", "")
    parts = [spec.get("title", ""), spec.get("summary", ""), field("Why it's trending")]
    if url := field("Page to open"):
        parts.append(f"Source: {url}")
    tags: list[str] = []
    for t in [*BASE_TAGS.get(topic, []), *map(hashtag, spec.get("tags", []))]:
        if len(t) > 1 and t.lower() not in {x.lower() for x in tags}:
            tags.append(t)
    return {"item": item, "caption": "\n\n".join(p for p in parts if p),
            "hashtags": tags[:MAX_TAGS]}  # fmt: skip


def start(state: Any, user: dict[str, Any], topic: str, day: str) -> dict[str, Any]:
    store = state.store
    req = request_for(store, user["id"], topic)
    row = store.add_autopilot(user["id"], day, topic, req)
    conv = services.create_conversation(state, user, f"Daily {topic} · {day}")
    sent = services.send_message(state, user, conversation_id=conv["id"], content=req,
                                 attachment_ids=[], job_input={"auto": True},
                                 source="autopilot")  # fmt: skip
    return store.update_autopilot(row["id"], conversation_id=conv["id"],
                                  job_id=sent["job"]["id"], status="running")  # fmt: skip


def sync(store: Any, row: dict[str, Any]) -> dict[str, Any]:
    """Fold a finished job into its autopilot row."""
    if row["status"] not in ACTIVE or not row["job_id"]:
        return row
    job = store.get_job(row["job_id"])
    if job is None or job["status"] in ("queued", "running", "waiting_input"):
        return row
    tut = next((t for t in store.list_tutorials(row["user_id"], row["conversation_id"])
                if t["job_id"] == job["id"]), None)  # fmt: skip
    out = (tut or {}).get("output") or {}
    if job["status"] != "succeeded" or not out.get("video") or tut["status"] != "ready":
        why = job.get("error") or (job.get("result") or {}).get("reason") or "no video was made"
        return store.update_autopilot(row["id"], status="failed", error=str(why)[:500],
                                      title=(tut or {}).get("title", ""))  # fmt: skip
    return store.update_autopilot(
        row["id"], status="ready", title=tut["title"], video_url=out["video"]["url"],
        thumbnail_url=(out.get("thumbnail") or {}).get("url", ""),
        **social(tut["spec"], row["topic"]),
    )  # fmt: skip


def tick(state: Any, now: datetime | None = None) -> list[dict[str, Any]]:
    """Start today's videos once the configured time has passed; update running ones."""
    store, settings = state.store, state.settings
    user = store.get_user_by_name(settings.admin_username)
    if user is None:
        return []
    for row in store.list_autopilot(user["id"]):
        if row["status"] in ACTIVE:
            sync(store, row)
    at = at_time(settings.autopilot_at)
    now = now or datetime.now(UTC)
    day = now.date().isoformat()
    if at is None or now.time() < at:
        return []
    done = {r["topic"] for r in store.list_autopilot(user["id"], day=day)}
    return [start(state, user, t, day) for t in TOPICS if t not in done]


async def loop(state: Any, every: float = 60) -> None:
    while True:
        try:
            for row in tick(state):
                log.info("autopilot started %s for %s (job %s)", row["topic"], row["day"],
                         row["job_id"])  # fmt: skip
        except Exception:
            log.exception("autopilot tick failed")
        await asyncio.sleep(every)
