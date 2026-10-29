"""Operator alerts: always an ERROR/WARNING line in the central log (logger `cueframe.alert`),
plus a POST to CUEFRAME_ALERT_WEBHOOK (Slack or Discord incoming webhook) when set.
Repeats of the same `key` are dropped for `every` seconds."""

import asyncio
import logging
import threading
import time
from typing import Any

import httpx

from app.log import fields, get_logger

log = get_logger("alert")
_sent: dict[str, float] = {}
_tasks: set[asyncio.Task] = set()


def alert(settings: Any, key: str, text: str, *, level: int = logging.ERROR,
          every: float = 3600, **kw: Any) -> bool:  # fmt: skip
    t = time.monotonic()
    if key in _sent and t - _sent[key] < every:
        return False
    _sent[key] = t
    log.log(level, f"ALERT {text}", extra=fields(alert=key, **kw))
    url = getattr(settings, "alert_webhook", "")
    if url:
        body = {"text": f"[Cueframe] {text}", "content": f"[Cueframe] {text}"}
        try:
            task = asyncio.get_running_loop().create_task(_post(url, body))
            _tasks.add(task)
            task.add_done_callback(_tasks.discard)
        except RuntimeError:
            threading.Thread(target=_post_sync, args=(url, body), daemon=True).start()
    return True


async def _post(url: str, body: dict) -> None:
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            await c.post(url, json=body)
    except Exception:
        log.warning("alert webhook failed", exc_info=True)


def _post_sync(url: str, body: dict) -> None:
    try:
        httpx.post(url, json=body, timeout=10)
    except Exception:
        log.warning("alert webhook failed", exc_info=True)


def reset() -> None:
    _sent.clear()
