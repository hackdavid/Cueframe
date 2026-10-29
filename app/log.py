"""Central logging: one format for the whole app, with the current user, auth session,
conversation and job attached to every record, plus structured key=value fields.
Records are also stored in the `logs` table, so they can be filtered by user and session.

    log = get_logger("agents.docs")
    log.info("pages fetched", extra=fields(count=3, ms=420))
"""

import contextvars
import json
import logging
import os
from typing import Any

job_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("job_id", default="-")
user_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("user_id", default="-")
session_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("session_id", default="-")
conversation_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "conversation_id", default="-"
)
ROOT = "cueframe"
_configured = False
_VARS = {
    "user_id": user_id_var,
    "session_id": session_id_var,
    "conversation_id": conversation_id_var,
    "job_id": job_id_var,
}


def fields(**kw: Any) -> dict[str, Any]:
    return {"fields": kw}


def bind(**ids: str | None) -> list[contextvars.Token]:
    """Set the ids for everything logged from here on in this context."""
    return [_VARS[k].set(v) for k, v in ids.items() if v]


def unbind(tokens: list[contextvars.Token]) -> None:
    for t in reversed(tokens):
        t.var.reset(t)


class _ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        extra = getattr(record, "fields", None) or {}
        for k, var in _VARS.items():
            setattr(record, k, extra.get(k) or var.get())  # explicit ids win
        record.kv = (
            " " + " ".join(f"{k}={json.dumps(v, default=str)}" for k, v in extra.items())
            if extra
            else ""
        )
        return True


class StoreHandler(logging.Handler):
    """Writes INFO+ records to the `logs` table (see `Store.list_logs` for the filters)."""

    def __init__(self, store: Any, level: int = logging.INFO):
        super().__init__(level)
        self.store = store
        self.addFilter(_ContextFilter())

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = record.getMessage()
            if record.exc_info:
                msg += "\n" + logging.Formatter().formatException(record.exc_info)
            ids = {k: getattr(record, k, "-") for k in _VARS}
            ids = {k: None if v == "-" else v for k, v in ids.items()}
            self.store.add_log({
                "ts": self.store_now(), "level": record.levelname, "levelno": record.levelno,
                "logger": record.name, "message": msg[:8000], **ids,
                "fields": json.dumps(getattr(record, "fields", None) or {}, default=str),
            })  # fmt: skip
        except Exception:
            self.handleError(record)

    @staticmethod
    def store_now() -> str:
        from app.db import now

        return now()


def attach_store(store: Any) -> StoreHandler:
    handler = StoreHandler(store)
    logging.getLogger(ROOT).addHandler(handler)
    return handler


def detach(handler: logging.Handler) -> None:
    logging.getLogger(ROOT).removeHandler(handler)


def setup_logging(level: str | None = None) -> None:
    global _configured
    if _configured:
        return
    handler = logging.StreamHandler()
    handler.addFilter(_ContextFilter())
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)-7s %(name)s [user=%(user_id)s job=%(job_id)s] "
            "%(message)s%(kv)s"
        )
    )
    root = logging.getLogger(ROOT)
    root.handlers[:] = [handler]
    root.setLevel(level or os.environ.get("CUEFRAME_LOG_LEVEL", "INFO"))
    root.propagate = False
    _configured = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"{ROOT}.{name}")
