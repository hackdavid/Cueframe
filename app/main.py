import logging
import re
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

import app.handlers.demo  # noqa: F401  (registers job handlers)
import app.handlers.research  # noqa: F401
import app.handlers.sandbox_run  # noqa: F401
from app import api, pages, ws
from app.auth import hash_password, user_for_token
from app.config import Settings, get_settings
from app.db import Store
from app.events import EventBus
from app.jobs import JobManager
from app.llm import MetaClient
from app.log import setup_logging
from app.sandbox.manager import SandboxManager
from app.storage import Storage

BASE = Path(__file__).parent


def _media_user(request: Request) -> dict | None:
    token = request.cookies.get("cf_session")
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        token = header[7:]
    return user_for_token(request.app.state.store, token)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    for path in (settings.uploads_dir, settings.artifacts_dir):
        path.mkdir(parents=True, exist_ok=True)

    @asynccontextmanager
    async def lifespan(app: FastAPI):  # type: ignore[no-untyped-def]
        store = Store(settings.db_path)
        store.upsert_user(settings.admin_username, hash_password(settings.admin_password))
        store.close_all_connections()
        bus = EventBus(store)
        jobs = JobManager(store, bus, settings)
        llm = MetaClient.from_settings(settings)
        jobs.llm = llm
        app.state.llm = llm
        jobs.recover()
        jobs.storage = Storage(settings)
        jobs.sandboxes = SandboxManager.from_settings(store, settings)
        await jobs.sandboxes.reap()
        app.state.sandboxes, app.state.storage = jobs.sandboxes, jobs.storage
        app.state.settings, app.state.store, app.state.bus, app.state.jobs = (
            settings,
            store,
            bus,
            jobs,
        )
        yield
        await jobs.shutdown()
        await jobs.sandboxes.shutdown()
        await llm.aclose()
        store.close()

    app = FastAPI(
        title="Cueframe API",
        description="Every UI action is a REST call or WebSocket frame, so the agent can be "
        "driven from Postman, scripts or MCP. Log in with `POST /api/auth/login`, then send "
        "`Authorization: Bearer <token>`.",
        lifespan=lifespan,
    )

    @app.middleware("http")
    async def protect_media(request: Request, call_next):  # type: ignore[no-untyped-def]
        if request.url.path.startswith("/media/") and _media_user(request) is None:
            return JSONResponse({"detail": "Not logged in"}, status_code=401)
        return await call_next(request)

    @app.get("/media/r2/{key:path}", include_in_schema=False)
    async def r2_media(key: str, request: Request):  # type: ignore[no-untyped-def]
        from fastapi.responses import RedirectResponse

        store, user = request.app.state.store, _media_user(request)
        prefix = request.app.state.settings.r2_prefix.strip("/")
        prefix = f"{re.escape(prefix)}/" if prefix else ""
        m = re.fullmatch(rf"{prefix}jobs/(j_[0-9a-f]+)/[A-Za-z0-9_-]+\.[a-z0-9]+", key)
        job = store.get_job(m.group(1)) if m else None
        cid = job and job["conversation_id"]
        if not user or not cid or not store.get_conversation(cid, user["id"]):
            return JSONResponse({"detail": "Not found"}, status_code=404)
        return RedirectResponse(request.app.state.storage.presign(key), status_code=307)

    app.include_router(api.router)
    app.include_router(ws.router)
    app.include_router(pages.router)
    app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
    app.mount("/media/uploads", StaticFiles(directory=settings.uploads_dir), name="uploads")
    app.mount("/media/artifacts", StaticFiles(directory=settings.artifacts_dir), name="artifacts")
    return app


logging.basicConfig(level=logging.INFO)
setup_logging()
app = create_app()
