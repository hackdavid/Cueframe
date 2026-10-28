"""Self-healing for tutorial jobs.

`retry` re-runs an operation that failed for a transient reason (timeouts, dropped
connections, HTTP 429/5xx, Modal/gRPC errors) with exponential backoff. `Healer`
keeps a log of every recovery a job made, shows each one live in the chat as a
"Healing" note, and stores the log with the tutorial so the final reply can list it.
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from app.log import fields, get_logger

log = get_logger("heal")
RETRY_HTTP = {408, 409, 425, 429, 500, 502, 503, 504}
BACKOFF = 1.0  # seconds before the first retry (tests set 0)


def short(exc: BaseException) -> str:
    text = str(exc).strip().splitlines()
    return f"{type(exc).__name__}: {text[0][:160] if text else ''}".rstrip(": ")


def transient(exc: BaseException) -> bool:
    """True when trying again could succeed without changing anything."""
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in RETRY_HTTP
    if isinstance(exc, httpx.TransportError | TimeoutError | ConnectionError):
        return True
    status = getattr(exc, "response", None)  # botocore ClientError
    if isinstance(status, dict):
        code = status.get("ResponseMetadata", {}).get("HTTPStatusCode")
        return code in RETRY_HTTP
    name, mod = type(exc).__name__, type(exc).__module__
    if any(w in name for w in ("Invalid", "NotFound", "Auth", "Permission", "Deprecat")):
        return False
    return mod.startswith(("modal", "grpc", "grpclib", "botocore", "urllib3"))


def anything(exc: BaseException) -> bool:
    return True


async def retry[T](
    fn: Callable[[], Awaitable[T]],
    what: str,
    attempts: int = 4,
    when: Callable[[BaseException], bool] = transient,
    on_retry: Callable[[str], None] | None = None,
) -> T:
    for i in range(attempts):
        try:
            return await fn()
        except Exception as exc:
            if i == attempts - 1 or not when(exc):
                raise
            msg = f"{what} failed ({short(exc)}); retrying ({i + 2}/{attempts})"
            log.warning("retry", extra=fields(what=what, attempt=i + 1, error=short(exc)))
            if on_retry:
                on_retry(msg)
            await asyncio.sleep(BACKOFF * 2**i)
    raise AssertionError("unreachable")


class Healer:
    """Collects the recoveries of one job; `log` is stored on the tutorial."""

    def __init__(self, ctx: Any, log: list[str] | None = None):
        self.ctx, self.log = ctx, log if log is not None else []
        self.save: Callable[[], Any] | None = None

    def __call__(self, text: str) -> None:
        self.log.append(text)
        if self.save:
            self.save()
        self.ctx.thinking("Healing", text)
        self.ctx.emit("tutorial.healing", {"note": text})

    def retry[T](self, fn: Callable[[], Awaitable[T]], what: str, **kw: Any) -> Awaitable[T]:
        return retry(fn, what, on_retry=self, **kw)

    def summary(self) -> str:
        if not self.log:
            return ""
        items = "\n".join(f"- {x}" for x in self.log)
        return f"**Fixed automatically while making this video:**\n{items}"
