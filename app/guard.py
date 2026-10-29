"""Abuse and cost guard: per-IP / per-account rate limits and the sign-up captcha.

Limits are in-memory sliding windows (one web process); the global daily spend cap lives in
`app.usage` because it has to be checked together with the plan limits."""

import threading
import time
from collections import defaultdict, deque
from typing import Any

import httpx
from fastapi import HTTPException, Request

from app.log import fields, get_logger

log = get_logger("guard")
TURNSTILE = "https://challenges.cloudflare.com/turnstile/v0/siteverify"


class RateLimiter:
    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def hit(self, key: str, limit: int, per: float) -> float | None:
        """Count one hit; None if allowed, else seconds until the next one is."""
        if limit <= 0:
            return None
        t = time.monotonic()
        with self._lock:
            q = self._hits[key]
            while q and q[0] <= t - per:
                q.popleft()
            if len(q) >= limit:
                return q[0] + per - t
            q.append(t)
            return None


def client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    return fwd.split(",")[0].strip() or (request.client.host if request.client else "-")


def check(limiter: RateLimiter | None, bucket: str, who: str, limit: int, per: float) -> None:
    """Raise 429 when `who` went over `limit` hits per `per` seconds in `bucket`."""
    if limiter is None:
        return
    wait = limiter.hit(f"{bucket}:{who}", limit, per)
    if wait is not None:
        log.warning("rate limited", extra=fields(bucket=bucket, who=who, limit=limit))
        secs = int(wait) + 1
        raise HTTPException(429, f"Too many requests. Try again in {secs} s.",
                            headers={"Retry-After": str(secs)})  # fmt: skip


async def captcha_ok(settings: Any, token: str | None, ip: str) -> bool:
    """Cloudflare Turnstile check; always passes when CUEFRAME_TURNSTILE_SECRET is unset."""
    if not settings.turnstile_secret:
        return True
    if not token:
        return False
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(TURNSTILE, data={"secret": settings.turnstile_secret,
                                              "response": token, "remoteip": ip})  # fmt: skip
        return bool(r.json().get("success"))
    except Exception:
        log.exception("captcha check failed")
        return False
