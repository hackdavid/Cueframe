"""Central logging: one format for the whole app, with the current job id and
structured key=value fields attached to every record.

    log = get_logger("agents.docs")
    log.info("pages fetched", extra=fields(count=3, ms=420))
"""

import contextvars
import json
import logging
import os
from typing import Any

job_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("job_id", default="-")
ROOT = "cueframe"
_configured = False


def fields(**kw: Any) -> dict[str, Any]:
    return {"fields": kw}


class _ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.job_id = job_id_var.get()
        extra = getattr(record, "fields", None)
        record.kv = (
            " " + " ".join(f"{k}={json.dumps(v, default=str)}" for k, v in extra.items())
            if extra
            else ""
        )
        return True


def setup_logging(level: str | None = None) -> None:
    global _configured
    if _configured:
        return
    handler = logging.StreamHandler()
    handler.addFilter(_ContextFilter())
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-7s %(name)s [job=%(job_id)s] %(message)s%(kv)s")
    )
    root = logging.getLogger(ROOT)
    root.handlers[:] = [handler]
    root.setLevel(level or os.environ.get("CUEFRAME_LOG_LEVEL", "INFO"))
    root.propagate = False
    _configured = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"{ROOT}.{name}")
