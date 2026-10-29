"""In-process background job runner.

Handlers are async functions registered with `@job_handler("kind")`. They get a
`JobContext` to report phase/progress, write artifacts and post chat replies.
Every state change is published on the event bus, so the UI can show progress
and a "finished" notification even after a page reload.
"""

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from app import alerts
from app.config import Settings
from app.db import Store, now
from app.events import EventBus
from app.log import bind, fields, get_logger, session_id_var

log = get_logger("jobs")


class JobFailed(RuntimeError):
    """Raise to fail a job while still persisting partial `result` (artifacts, logs)."""

    def __init__(self, message: str, result: dict[str, Any]):
        super().__init__(message)
        self.result = result


Handler = Callable[["JobContext"], Awaitable[dict[str, Any] | None]]
HANDLERS: dict[str, Handler] = {}


def job_handler(kind: str) -> Callable[[Handler], Handler]:
    def register(fn: Handler) -> Handler:
        HANDLERS[kind] = fn
        return fn

    return register


class JobContext:
    def __init__(self, manager: "JobManager", job: dict[str, Any]):
        self.holds_slot = True
        self._manager = manager
        self.job = job
        self.id: str = job["id"]
        self.conversation_id: str | None = job["conversation_id"]
        self.user_id: str | None = job.get("user_id")
        self.session_id: str | None = job.get("session_id")
        self.input: dict[str, Any] = job["input"]
        self.settings = manager.settings
        self.store = manager.store
        self.llm = manager.llm

    def _update(self, **fields: Any) -> None:
        self.job = self.store.update_job(self.id, **fields)

    def emit(self, type_: str, payload: dict[str, Any] | None = None) -> None:
        self._manager.bus.publish(
            type_,
            {"job_id": self.id, **(payload or {})},
            conversation_id=self.conversation_id,
            job_id=self.id,
        )

    def progress(self, value: float, label: str | None = None) -> None:
        self._update(progress=max(0.0, min(1.0, value)), label=label or self.job["label"])
        self.emit("job.progress", {"progress": self.job["progress"], "label": self.job["label"]})

    def phase(self, phase: str, label: str | None = None) -> None:
        self._update(phase=phase, label=label or self.job["label"])
        self.emit("phase.changed", {"phase": phase, "label": self.job["label"]})

    def thinking(self, role: str, label: str) -> None:
        self._update(label=label)
        self.emit("agent.thinking", {"role": role, "label": label})

    async def ask(self, question: str, options: list[str], key: str = "") -> str:
        """Pause the job until the user answers (UI, REST `POST /api/jobs/{id}/answer`
        or WS `job.answer`). The question is also posted in the chat."""
        input_id = key or f"in_{uuid.uuid4().hex[:8]}"
        pending = {"input_id": input_id, "question": question, "options": options}
        fut: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        self._manager._waiters[self.id] = (input_id, fut)
        if self.conversation_id:
            opts = "\n".join(f"- {o}" for o in options)
            self.reply(f"**Question:** {question}\n\n{opts}")
        self._update(status="waiting_input", pending_input=pending)
        self.emit("job.input_required", {"job": self.job, **pending})
        # Waiting on a human must not hold a worker slot.
        self._manager._slots.release()
        self.holds_slot = False
        try:
            value = await asyncio.wait_for(fut, self.settings.ask_timeout_seconds)
        except TimeoutError as exc:
            raise RuntimeError(f"No answer to {question!r} within the time limit") from exc
        finally:
            self._manager._waiters.pop(self.id, None)
            await self._manager._slots.acquire()  # cancellation here leaves holds_slot False
            self.holds_slot = True
        self._update(status="running", pending_input=None)
        self.emit("job.input_received", {"job": self.job, "input_id": input_id, "value": value})
        return value

    def artifact_dir(self) -> Path:
        path = self.settings.artifacts_dir / self.id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def artifact_url(self, path: Path) -> str:
        return "/media/artifacts/" + path.relative_to(self.settings.artifacts_dir).as_posix()

    def reply(
        self, content: str, attachments: list[dict[str, Any]] | None = None
    ) -> dict[str, Any]:
        if not self.conversation_id:
            raise RuntimeError("Job has no conversation to reply to")
        message = self.store.add_message(
            self.conversation_id, "assistant", content, attachments, job_id=self.id, source="agent"
        )
        self._manager.bus.publish(
            "message.created", {"message": message}, conversation_id=self.conversation_id
        )
        return message


class JobManager:
    def __init__(self, store: Store, bus: EventBus, settings: Settings):
        self.store = store
        self.bus = bus
        self.settings = settings
        self.llm: Any = None
        self.sandboxes: Any = None
        self.storage: Any = None
        self._waiters: dict[str, tuple[str, asyncio.Future[str]]] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._slots = asyncio.Semaphore(settings.max_concurrent_jobs)

    def recover(self) -> list[dict[str, Any]]:
        """Mark jobs cut off by a restart as interrupted; returns them."""
        jobs = self.store.interrupt_stale_jobs()
        for job in jobs:
            self._publish_job("job.interrupted", job)
        return jobs

    def submit(
        self,
        kind: str,
        payload: dict[str, Any],
        conversation_id: str | None,
        connection_id: str | None = None,
        *,
        user_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        if kind not in HANDLERS:
            raise KeyError(f"Unknown job kind: {kind}")
        if user_id is None and conversation_id:
            conv = self.store.get_conversation(conversation_id)
            user_id = conv and conv["user_id"]
        if session_id is None and session_id_var.get() != "-":
            session_id = session_id_var.get()
        job = self.store.create_job(
            conversation_id, kind, payload, connection_id, user_id=user_id, session_id=session_id
        )
        self._publish_job("job.queued", job)
        self._tasks[job["id"]] = asyncio.create_task(self._run(job), name=f"job:{job['id']}")
        return job

    def cancel(self, job_id: str) -> bool:
        task = self._tasks.get(job_id)
        if task is None or task.done():
            return False
        task.cancel()
        return True

    def answer(self, job_id: str, input_id: str, value: str) -> dict[str, Any]:
        waiter = self._waiters.get(job_id)
        if waiter is None or waiter[0] != input_id:
            raise LookupError("This job is not waiting for that input")
        job = self.store.get_job(job_id) or {}
        options = (job.get("pending_input") or {}).get("options") or []
        if options and value not in options:
            raise ValueError(f"Answer must be one of: {options}")
        if not waiter[1].done():
            waiter[1].set_result(value)
        return job

    async def shutdown(self) -> None:
        for task in self._tasks.values():
            task.cancel()
        await asyncio.gather(*self._tasks.values(), return_exceptions=True)

    async def _run(self, job: dict[str, Any]) -> None:
        bind(job_id=job["id"], user_id=job.get("user_id"), session_id=job.get("session_id"),
             conversation_id=job.get("conversation_id"))  # fmt: skip
        try:
            await self._slots.acquire()
            ctx = JobContext(self, job)
            try:
                job = self.store.update_job(job["id"], status="running", started_at=now())
                self._publish_job("job.started", job)
                result = await HANDLERS[job["kind"]](ctx)
            finally:
                if ctx.holds_slot:
                    self._slots.release()
            job = self.store.update_job(
                job["id"], status="succeeded", progress=1.0, result=result or {}, finished_at=now()
            )
            if self.store.finish_usage(job["id"], "succeeded", _usage_detail(result)) and (
                ref := self.store.credit_referral(job["user_id"], self.settings.referral_bonus)
            ):
                bonus = self.settings.referral_bonus
                log.info("referral credited", extra=fields(referrer=ref, bonus=bonus))
            self._publish_job("job.succeeded", job)
        except asyncio.CancelledError:
            job = self.store.update_job(job["id"], status="cancelled", finished_at=now())
            self.store.finish_usage(job["id"], "cancelled")
            self._publish_job("job.cancelled", job)
        except Exception as exc:
            log.exception("Job %s failed", job["id"])
            job = self.store.update_job(
                job["id"],
                status="failed",
                error=f"{type(exc).__name__}: {exc}",
                result=getattr(exc, "result", None),
                finished_at=now(),
            )
            self.store.finish_usage(job["id"], "failed", {"error": job["error"][:300]})
            if self.settings.sentry_dsn:
                import sentry_sdk

                sentry_sdk.capture_exception(exc)
            alerts.alert(self.settings, f"job:{job['id']}",
                         f"{job['kind']} job {job['id']} failed: {job['error'][:300]}",
                         kind=job["kind"])  # fmt: skip
            self._publish_job("job.failed", job)
        finally:
            self._tasks.pop(job["id"], None)
            log.info("job finished", extra=fields(kind=job["kind"], status=job["status"]))

    def _publish_job(self, type_: str, job: dict[str, Any]) -> None:
        self.bus.publish(
            type_, {"job": job}, conversation_id=job["conversation_id"], job_id=job["id"]
        )


def _usage_detail(result: dict[str, Any] | None) -> dict[str, Any] | None:
    keys = ("tutorial_id", "title", "duration", "seconds", "video", "url", "limit_reached")
    picked = {k: result[k] for k in keys if isinstance(result, dict) and k in result}
    return {k: v for k, v in picked.items() if isinstance(v, str | int | float)} or None
