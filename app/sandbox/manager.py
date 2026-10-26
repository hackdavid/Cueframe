"""Owns every live sandbox. Guarantees shutdown on: job end (success, failure,
cancel), idle, hard max lifetime (also enforced by Modal itself), server
start (reap leftovers) and server stop."""

import asyncio
import contextlib
import time
from collections.abc import AsyncIterator
from typing import Any

from app.config import Settings
from app.db import Store, now
from app.log import fields, get_logger
from app.sandbox.base import Sandbox

log = get_logger("sandbox")


class SandboxManager:
    def __init__(self, store: Store, settings: Settings, provider: Any = None):
        self.store = store
        self.settings = settings
        self.provider = provider
        self._live: dict[str, Sandbox] = {}
        self._by_job: dict[str, str] = {}
        self._started: dict[str, float] = {}
        self._touched: dict[str, float] = {}
        self._watchdogs: dict[str, asyncio.Task[None]] = {}

    @classmethod
    def from_settings(cls, store: Store, settings: Settings) -> "SandboxManager":
        if settings.sandbox_provider == "fake":
            from app.sandbox.fake import FakeSandbox

            return cls(store, settings, FakeSandbox)
        from app.sandbox.modal_box import ModalSandbox

        return cls(store, settings, ModalSandbox)

    def for_job(self, job_id: str) -> tuple[str, Sandbox] | None:
        sid = self._by_job.get(job_id)
        return (sid, self._live[sid]) if sid and sid in self._live else None

    def touch(self, sid: str) -> None:
        self._touched[sid] = time.monotonic()

    @contextlib.asynccontextmanager
    async def session(
        self, job_id: str, conversation_id: str | None, start_url: str = "about:blank"
    ) -> AsyncIterator[tuple[str, Sandbox]]:
        rec = self.store.create_sandbox(job_id, conversation_id, self.provider.provider)
        sid = rec["id"]
        self.store.update_job(job_id, sandbox_id=sid)
        try:
            box = await self.provider.create(self.settings, start_url)
        except BaseException as exc:
            self.store.update_sandbox(
                sid, status="failed", reason=f"{type(exc).__name__}: {exc}"[:300],
                terminated_at=now(),
            )  # fmt: skip
            raise
        self._live[sid], self._by_job[job_id] = box, sid
        self._started[sid] = self._touched[sid] = time.monotonic()
        self.store.update_sandbox(
            sid, status="running", provider_id=box.provider_id, live_url=box.live_url
        )
        self._watchdogs[sid] = asyncio.create_task(self._watch(sid))
        reason = "job finished"
        try:
            yield sid, box
        except asyncio.CancelledError:
            reason = "job cancelled"
            raise
        except BaseException:
            reason = "job failed"
            raise
        finally:
            await asyncio.shield(self.stop(sid, reason))

    async def stop(self, sid: str, reason: str) -> dict[str, Any] | None:
        box = self._live.pop(sid, None)
        dog = self._watchdogs.pop(sid, None)
        if dog and dog is not asyncio.current_task():
            dog.cancel()
        for job, s in list(self._by_job.items()):
            if s == sid:
                del self._by_job[job]
        if box is None:
            return self.store.get_sandbox(sid)
        try:
            await box.terminate()
        except Exception:
            log.exception("terminate failed for %s", sid)
        secs = time.monotonic() - self._started.pop(sid, time.monotonic())
        self._touched.pop(sid, None)
        cost = round(secs / 3600 * self.settings.sandbox_usd_per_hour, 5)
        log.info("sandbox terminated", extra=fields(sandbox=sid, reason=reason, seconds=secs))
        return self.store.update_sandbox(
            sid, status="terminated", reason=reason, seconds=round(secs, 1),
            est_cost_usd=cost, terminated_at=now(),
        )  # fmt: skip

    async def _watch(self, sid: str) -> None:
        while sid in self._live:
            await asyncio.sleep(5)
            idle = time.monotonic() - self._touched.get(sid, 0)
            age = time.monotonic() - self._started.get(sid, 0)
            if idle > self.settings.sandbox_idle_seconds:
                await self.stop(sid, "idle timeout")
            elif age > self.settings.sandbox_max_seconds:
                await self.stop(sid, "max lifetime")

    async def reap(self) -> None:
        """On server start: anything not terminated belongs to a dead process."""
        for rec in self.store.list_sandboxes(active_only=True):
            if rec["provider"] == "modal" and rec["provider_id"]:
                with contextlib.suppress(Exception):
                    from app.sandbox.modal_box import terminate_by_id

                    await terminate_by_id(self.settings, rec["provider_id"])
            self.store.update_sandbox(
                rec["id"], status="terminated", reason="reaped on server start", terminated_at=now()
            )
        if self.provider is not None and self.provider.provider == "modal":
            with contextlib.suppress(Exception):
                from app.sandbox.modal_box import reap_app

                killed = await reap_app(self.settings)
                if killed:
                    log.warning("reaped orphan sandboxes", extra=fields(ids=killed))

    async def shutdown(self) -> None:
        await asyncio.gather(
            *(self.stop(sid, "server shutdown") for sid in list(self._live)),
            return_exceptions=True,
        )
