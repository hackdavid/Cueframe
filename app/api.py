import asyncio
from typing import Annotated, Any, Literal

import httpx
import websockets
from fastapi import (
    APIRouter,
    File,
    Header,
    HTTPException,
    Request,
    Response,
    UploadFile,
    WebSocket,
)
from pydantic import BaseModel, Field

from app import auth, guard, services, usage
from app.agents import feed
from app.agents.contracts import ResearchRequest, ResearchResult
from app.agents.docs_research import DocsResearchAgent
from app.auth import Admin, User, websocket_user
from app.llm import LLMError
from app.log import fields, get_logger
from app.plans import plans_known
from app.sandbox.contracts import Action, Answer
from app.tutorial.pipeline import StateEdit
from app.uploads import save_upload

log = get_logger("api")
router = APIRouter(prefix="/api")


class LoginIn(BaseModel):
    username: str
    password: str


class ConversationIn(BaseModel):
    title: str = "New chat"


class ConversationPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    voice_id: str | None = None  # narrator for this conversation; "" clears the pick


class MessageIn(BaseModel):
    content: str = ""
    attachment_ids: list[str] = []
    job_kind: str = services.DEFAULT_JOB
    job_input: dict[str, Any] = {}


ConnectionId = Annotated[
    str | None,
    Header(description="Optional WebSocket connection id; links the job to that client"),
]


def _s(request: Request) -> Any:
    return request.app.state


@router.get("/health", tags=["meta"])
def health() -> dict[str, str]:
    return {"status": "ok"}


# ---------- auth ----------
class SignupIn(BaseModel):
    username: str = Field(min_length=3, max_length=60, pattern=r"^[\w.@+-]+$")
    password: str = Field(min_length=8, max_length=200)
    captcha: str | None = None
    ref: str | None = Field(default=None, max_length=40)


def _set_session(request: Request, response: Response, token: str) -> None:
    response.set_cookie(auth.COOKIE, token, httponly=True, samesite="lax",
                        secure=_s(request).settings.cookie_secure,
                        max_age=60 * 60 * 24 * 30)  # fmt: skip


@router.post("/auth/signup", status_code=201, tags=["auth"])
async def signup(request: Request, response: Response, body: SignupIn) -> dict[str, Any]:
    """Create a Free account (when CUEFRAME_OPEN_SIGNUP is on). Rate-limited per IP and
    behind a Cloudflare Turnstile captcha when it's configured. `ref` (or the `cf_ref`
    cookie from a /r/CODE link) credits the inviting user."""
    s, ip = _s(request), guard.client_ip(request)
    if not s.settings.open_signup:
        raise HTTPException(403, "Sign-up is closed")
    guard.check(getattr(s, "limiter", None), "signup", ip, s.settings.rate_signup_per_hour, 3600)
    if not await guard.captcha_ok(s.settings, body.captcha, ip):
        raise HTTPException(400, "Please complete the captcha")
    try:
        token, user = auth.signup(s.store, body.username, body.password,
                                  plan=s.settings.default_plan,
                                  ref=body.ref or request.cookies.get("cf_ref"))  # fmt: skip
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    _set_session(request, response, token)
    return {"token": token, "user": user}


@router.post("/auth/login", tags=["auth"])
def login(request: Request, response: Response, body: LoginIn) -> dict[str, Any]:
    s = _s(request)
    guard.check(getattr(s, "limiter", None), "login", guard.client_ip(request),
                s.settings.rate_login_per_min, 60)  # fmt: skip
    result = auth.login(_s(request).store, body.username, body.password)
    if result is None:
        raise HTTPException(401, "Wrong username or password")
    token, user = result
    response.set_cookie(
        auth.COOKIE,
        token,
        httponly=True,
        samesite="lax",
        secure=_s(request).settings.cookie_secure,
        max_age=60 * 60 * 24 * 30,
    )
    return {"token": token, "token_type": "bearer", "user": user}


@router.post("/auth/logout", tags=["auth"])
def logout(request: Request, response: Response, user: User) -> dict[str, bool]:
    token = auth.request_token(request, None)
    auth_header = request.headers.get("authorization", "")
    if auth_header.lower().startswith("bearer "):
        token = auth_header[7:]
    if token:
        auth.logout(_s(request).store, token)
    response.delete_cookie(auth.COOKIE)
    return {"ok": True}


@router.get("/auth/me", tags=["auth"])
def me(user: User) -> dict[str, Any]:
    return user


# ---------- conversations ----------
@router.get("/conversations", tags=["conversations"])
def list_conversations(request: Request, user: User) -> list[dict[str, Any]]:
    return _s(request).store.list_conversations(user["id"])


@router.post("/conversations", status_code=201, tags=["conversations"])
def create_conversation(
    request: Request, user: User, body: ConversationIn | None = None
) -> dict[str, Any]:
    return services.create_conversation(_s(request), user, (body or ConversationIn()).title)


@router.get("/conversations/{cid}", tags=["conversations"])
def get_conversation(request: Request, cid: str, user: User) -> dict[str, Any]:
    """Full state: messages, jobs, `active_jobs` / `has_pending_job`, live connections."""
    return services.conversation_state(_s(request), cid, user)


@router.patch("/conversations/{cid}", tags=["conversations"])
def rename_conversation(
    request: Request, cid: str, body: ConversationPatch, user: User
) -> dict[str, Any]:
    s = _s(request)
    services.conversation_or_404(s, cid, user)
    if body.title is None and body.voice_id is None:
        raise HTTPException(422, "Nothing to update")
    s.store.update_conversation(
        cid,
        **({"title": body.title} if body.title is not None else {}),
        **({"voice_id": body.voice_id or None} if body.voice_id is not None else {}),
    )
    conv = s.store.get_conversation(cid)
    s.bus.publish("conversation.updated", {"conversation": conv}, conversation_id=cid)
    return conv


@router.get("/voices", tags=["conversations"])
async def list_voices(user: User, language: str = "") -> list[dict[str, Any]]:
    """Voices for the picker: the account's library by default, or the ElevenLabs
    community library for a language (`?language=hi`). Cached server-side. Shared
    voice ids work for TTS directly — nothing needs adding to the account."""
    from app import voices

    try:
        if language:
            return await voices.search_shared(language)
        return await voices.list_voices()
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc


@router.delete("/conversations/{cid}", status_code=204, tags=["conversations"])
async def delete_conversation(request: Request, cid: str, user: User) -> None:
    s = _s(request)
    services.conversation_or_404(s, cid, user)
    for job in s.store.list_jobs(cid, active_only=True):
        s.jobs.cancel(job["id"])
    s.store.delete_conversation(cid)
    s.bus.publish("conversation.deleted", {"conversation_id": cid, "user_id": user["id"]})


@router.post("/conversations/{cid}/messages", status_code=201, tags=["conversations"])
async def post_message(
    request: Request,
    cid: str,
    body: MessageIn,
    user: User,
    x_connection_id: ConnectionId = None,
) -> dict[str, Any]:
    """Send a user message; starts a background job. Returns `{conversation, message, job}`."""
    return _send(request, user, cid, body, x_connection_id)


@router.post("/messages", status_code=201, tags=["conversations"])
async def post_message_new_conversation(
    request: Request, body: MessageIn, user: User, x_connection_id: ConnectionId = None
) -> dict[str, Any]:
    """Same as above but creates a new conversation first."""
    return _send(request, user, None, body, x_connection_id)


def _send(
    request: Request, user: dict[str, Any], cid: str | None, body: MessageIn, conn_id: str | None
) -> dict[str, Any]:
    s = _s(request)
    conn = s.store.get_connection(conn_id) if conn_id else None
    if conn and conn["user_id"] != user["id"]:
        conn = None
    return services.send_message(
        s,
        user,
        conversation_id=cid,
        content=body.content,
        attachment_ids=body.attachment_ids,
        job_kind=body.job_kind,
        job_input=body.job_input,
        source=(conn["client"] or "ws") if conn else "api",
        connection_id=conn["id"] if conn else None,
    )


# ---------- uploads ----------
@router.post("/uploads", status_code=201, tags=["uploads"])
async def upload_files(
    request: Request, user: User, files: Annotated[list[UploadFile], File()]
) -> list[dict[str, Any]]:
    """Multipart upload (field name `files`, repeatable). Use the returned ids as
    `attachment_ids` when sending a message."""
    s = _s(request)
    return [s.store.add_upload(await save_upload(f, s.settings, user["id"])) for f in files]


# ---------- jobs ----------
@router.get("/jobs", tags=["jobs"])
def list_jobs(
    request: Request, user: User, conversation_id: str | None = None, active: bool = False
) -> list[dict[str, Any]]:
    return _s(request).store.list_jobs(conversation_id, active_only=active, user_id=user["id"])


def _job_or_404(request: Request, jid: str, user: dict[str, Any]) -> dict[str, Any]:
    s = _s(request)
    job = s.store.get_job(jid)
    if job is None or s.store.get_conversation(job["conversation_id"], user["id"]) is None:
        raise HTTPException(404, "Job not found")
    return job


@router.get("/jobs/{jid}", tags=["jobs"])
def get_job(request: Request, jid: str, user: User) -> dict[str, Any]:
    return _job_or_404(request, jid, user)


@router.post("/jobs/{jid}/cancel", tags=["jobs"])
async def cancel_job(request: Request, jid: str, user: User) -> dict[str, Any]:
    _job_or_404(request, jid, user)
    return {"cancelled": _s(request).jobs.cancel(jid)}


# ---------- events & connections ----------
@router.get("/events", tags=["events"])
def event_history(
    request: Request, user: User, after: int = 0, conversation_id: str | None = None
) -> list[dict[str, Any]]:
    """Polling alternative to the WebSocket: events with `seq > after`."""
    return _s(request).store.events_after(after, user["id"], conversation_id)


@router.get("/connections", tags=["events"])
def list_connections(
    request: Request, user: User, conversation_id: str | None = None, active: bool = True
) -> list[dict[str, Any]]:
    return _s(request).store.list_connections(user["id"], conversation_id, active)


@router.post("/research/steps", response_model=ResearchResult, tags=["agents"])
async def research_steps(body: ResearchRequest, request: Request, user: User) -> ResearchResult:
    """Run the docs research agent synchronously (about 20-60 s). For a background run with
    live progress, send `POST /api/messages` with `job_kind: "docs.research"`."""
    state = _s(request)
    if not state.llm.configured:
        raise HTTPException(503, "MODEL_API_KEY is not configured")
    try:
        return await DocsResearchAgent(state.llm, state.settings).run(body)
    except LLMError as exc:
        raise HTTPException(502, f"Model call failed: {exc}") from exc


@router.post("/jobs/{jid}/answer", tags=["jobs"])
async def answer_job(request: Request, jid: str, body: Answer, user: User) -> dict[str, Any]:
    """Answer a job that is `waiting_input` (see its `pending_input`)."""
    _job_or_404(request, jid, user)
    return services.answer_job(_s(request), jid, body.input_id, body.value)


# ---------- sandboxes ----------
@router.get("/sandboxes", tags=["sandboxes"])
def list_sandboxes(request: Request, user: User, active: bool = False) -> list[dict[str, Any]]:
    return _s(request).store.list_sandboxes(active_only=active, user_id=user["id"])


def _live_box(request: Request, jid: str, user: dict[str, Any]) -> tuple[str, Any]:
    _job_or_404(request, jid, user)
    live = _s(request).sandboxes.for_job(jid)
    if live is None:
        raise HTTPException(409, "This job has no running sandbox")
    return live


@router.post("/jobs/{jid}/sandbox/actions", tags=["sandboxes"])
async def sandbox_action(request: Request, jid: str, body: Action, user: User) -> dict[str, Any]:
    """Send one command to the job's live sandbox and get the timed result back."""
    sid, box = _live_box(request, jid, user)
    async with _s(request).sandboxes.busy(sid):
        return (await box.run(-1, body)).model_dump()


def _upstream(app: Any, jid: str) -> str:
    if not app.state.settings.live_view:
        raise HTTPException(409, "The live sandbox view is disabled")
    live = app.state.sandboxes.for_job(jid)
    if live is None or not live[1].live_url:
        raise HTTPException(409, "This job has no running sandbox")
    return live[1].live_url.split("/vnc.html")[0]


@router.get("/jobs/{jid}/live/{path:path}", tags=["sandboxes"], include_in_schema=False)
async def live_view(request: Request, jid: str, path: str, user: User) -> Response:
    """The noVNC page and its assets for the job's live desktop (owner only)."""
    _job_or_404(request, jid, user)
    if ".." in path.split("/"):
        raise HTTPException(404)
    base = _upstream(request.app, jid)
    async with httpx.AsyncClient(timeout=20) as http:
        r = await http.get(f"{base}/{path}")
    return Response(r.content, r.status_code, media_type=r.headers.get("content-type"),
                    headers={"Cache-Control": "private, max-age=300"})  # fmt: skip


@router.websocket("/jobs/{jid}/live/websockify")
async def live_socket(ws: WebSocket, jid: str) -> None:
    """Relay the view-only VNC stream so the provider address never reaches the browser."""
    user = websocket_user(ws)
    job = ws.app.state.store.get_job(jid)
    if (
        user is None
        or job is None
        or not ws.app.state.store.get_conversation(job["conversation_id"], user["id"])
    ):
        await ws.close(4404)
        return
    try:
        base = _upstream(ws.app, jid)
    except HTTPException:
        await ws.close(4409)
        return
    proto = "binary" if "binary" in ws.scope.get("subprotocols", []) else None
    await ws.accept(subprotocol=proto)
    url = "ws" + base.removeprefix("http") + "/websockify"
    async with websockets.connect(url, subprotocols=["binary"], max_size=None) as up:

        async def down() -> None:
            try:
                async for msg in up:
                    await (ws.send_bytes(msg) if isinstance(msg, bytes) else ws.send_text(msg))
            except websockets.ConnectionClosed:
                pass  # sandbox ended or browser left: upstream dropped without a close frame

        async def upward() -> None:
            while True:
                m = await ws.receive()
                if m["type"] == "websocket.disconnect":
                    return
                await up.send(m.get("bytes") or m.get("text") or b"")

        tasks = [asyncio.create_task(down()), asyncio.create_task(upward())]
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for t in done:
            t.exception()  # retrieve so asyncio doesn't log "exception was never retrieved"
        for t in pending:
            t.cancel()


@router.get("/jobs/{jid}/sandbox/screenshot", tags=["sandboxes"])
async def sandbox_screenshot(request: Request, jid: str, user: User) -> Response:
    sid, box = _live_box(request, jid, user)
    _s(request).sandboxes.touch(sid)
    return Response(await box.screenshot(), media_type="image/png")


@router.post("/sandboxes/{sid}/stop", tags=["sandboxes"])
async def stop_sandbox(request: Request, sid: str, user: User) -> dict[str, Any]:
    s = _s(request)
    rec = s.store.get_sandbox(sid)
    if rec is None or (rec["job_id"] and not s.store.get_job(rec["job_id"])):
        raise HTTPException(404, "Sandbox not found")
    if rec["job_id"]:
        _job_or_404(request, rec["job_id"], user)
    return await s.sandboxes.stop(sid, "stopped by user") or rec


class StateEditIn(StateEdit):
    state: str = ""


# ---------- tutorials ----------


def _tutorial_or_404(request: Request, tid: str, user: dict[str, Any]) -> dict[str, Any]:
    tut = _s(request).store.get_tutorial(tid, user["id"])
    if tut is None:
        raise HTTPException(404, "Tutorial not found")
    return tut


@router.get("/tutorials", tags=["tutorials"])
def list_tutorials(
    request: Request, user: User, conversation_id: str | None = None
) -> list[dict[str, Any]]:
    """Tutorials with their state graph, edit inputs and output URLs."""
    return _s(request).store.list_tutorials(user["id"], conversation_id)


@router.get("/tutorials/{tid}", tags=["tutorials"])
def get_tutorial(request: Request, tid: str, user: User) -> dict[str, Any]:
    return _tutorial_or_404(request, tid, user)


def _edit_job(request: Request, tut: dict[str, Any], text: str, edits: list) -> dict[str, Any]:
    payload = {"text": text, "attachments": [],
               "job_input": {"tutorial_id": tut["id"], "edits": edits}}  # fmt: skip
    return _s(request).jobs.submit("tutorial.edit", payload, tut["conversation_id"],
                                   user_id=tut["user_id"])  # fmt: skip


@router.patch("/tutorials/{tid}/states/{state_id}", status_code=202, tags=["tutorials"])
async def edit_state(
    request: Request, tid: str, state_id: str, body: StateEditIn, user: User
) -> dict[str, Any]:
    """Change one state (narration, label, zoom, highlight, cut, pace). Starts a job
    that re-voices and re-renders only what changed, then posts the new version."""
    tut = _tutorial_or_404(request, tid, user)
    states = {s["id"] for s in (tut.get("graph") or {}).get("states", [])}
    states |= set((tut.get("spec") or {}).get("hidden") or [])  # cut steps can come back
    if state_id not in states:
        raise HTTPException(404, f"Unknown state; states are {sorted(states)}")
    edit = {**body.model_dump(exclude_none=True, exclude={"state"}), "state": state_id}
    return {"job": _edit_job(request, tut, f"Edit state {state_id}", [edit])}


@router.post("/tutorials/{tid}/render", status_code=202, tags=["tutorials"])
async def rerender(request: Request, tid: str, user: User) -> dict[str, Any]:
    """Render the current graph again (unchanged segments come from the cache)."""
    return {"job": _edit_job(request, _tutorial_or_404(request, tid, user), "Re-render", [])}


@router.get("/trending", tags=["agents"])
async def trending(user: User, niche: str = "all") -> list[dict[str, Any]]:
    """Today's trending repos, papers, models and tech news, each with video prompts."""
    return await feed.trending(niche)


# ---------- usage, plans and central logs ----------
class UserIn(BaseModel):
    username: str = Field(min_length=2, max_length=80)
    password: str = Field(min_length=8, max_length=200)
    plan: str = "free"
    role: Literal["user", "admin"] = "user"


class UserPatch(BaseModel):
    plan: str | None = None
    role: Literal["user", "admin"] | None = None


def _plan_or_422(request: Request, plan: str | None) -> None:
    if plan is not None and plan not in plans_known(_s(request).settings.plan_limits):
        raise HTTPException(422, f"Unknown plan: {plan}")


@router.get("/usage", tags=["usage"])
def my_usage(request: Request, user: User) -> dict[str, Any]:
    """Your plan, its monthly limits, what you've used and what's left."""
    s = _s(request)
    me = s.store.get_user(user["id"]) or user
    out = usage.summary(s.store, s.settings, me)
    out["recent"] = s.store.list_usage({"user_id": user["id"]}, limit=20)
    code = s.store.ensure_ref_code(user["id"])
    out["referral"] = {"code": code, "url": f"{s.settings.public_url.rstrip('/')}/r/{code}",
                       "bonus_per_friend": s.settings.referral_bonus,
                       **s.store.referral_counts(user["id"])}  # fmt: skip
    out["watermark"] = out["plan"] in s.settings.watermark_plans
    return out


class ShareIn(BaseModel):
    public: bool = True


@router.post("/tutorials/{tid}/share", tags=["tutorials"])
def share_tutorial(request: Request, tid: str, body: ShareIn, user: User) -> dict[str, Any]:
    """Turn the public share page (/v/SLUG) of a finished video on or off."""
    s = _s(request)
    tut = _tutorial_or_404(request, tid, user)
    if not (tut.get("output") or {}).get("video"):
        raise HTTPException(409, "The video isn't ready yet")
    slug = s.store.share_tutorial(tid, body.public)
    log.info("share", extra=fields(tutorial_id=tid, public=body.public))
    return {"public": body.public, "slug": slug,
            "url": f"{s.settings.public_url.rstrip('/')}/v/{slug}"}  # fmt: skip


@router.get("/admin/stats", tags=["admin"])
def admin_stats(request: Request, admin: Admin, hours: int = 24) -> dict[str, Any]:
    """Monitoring: jobs by kind (statuses, success rate, avg/p95 seconds, top errors), today's
    usage and estimated spend against the daily cap, and running sandboxes."""
    from datetime import UTC, datetime, timedelta

    s = _s(request)
    since = (datetime.now(UTC) - timedelta(hours=max(1, min(hours, 24 * 31)))).isoformat(
        timespec="milliseconds"
    )
    return {
        "since": since,
        "jobs": s.store.job_stats(since),
        "usage_today": s.store.usage_totals({"since": usage.today()}),
        "spend_today_usd": usage.spend(s.store, s.settings),
        "spend_cap_usd": s.settings.daily_spend_cap_usd,
        "sandboxes_active": len(s.store.list_sandboxes(active_only=True)),
        "alerts": s.store.list_logs({"logger": "cueframe.alert", "since": since}, 20),
        "sentry": bool(s.settings.sentry_dsn),
        "alert_webhook": bool(s.settings.alert_webhook),
    }


@router.get("/admin/usage", tags=["admin"])
def admin_usage(
    request: Request,
    admin: Admin,
    user_id: str | None = None,
    session_id: str | None = None,
    conversation_id: str | None = None,
    job_id: str | None = None,
    category: str | None = None,
    kind: str | None = None,
    status: str | None = None,
    plan: str | None = None,
    since: str | None = None,
    until: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """Usage records (newest first) and totals by category and status, filtered."""
    f = {"user_id": user_id, "session_id": session_id, "conversation_id": conversation_id,
         "job_id": job_id, "category": category, "kind": kind, "status": status, "plan": plan,
         "since": since, "until": until}  # fmt: skip
    store = _s(request).store
    return {"records": store.list_usage(f, limit), "totals": store.usage_totals(f)}


@router.get("/admin/logs", tags=["admin"])
def admin_logs(
    request: Request,
    admin: Admin,
    user_id: str | None = None,
    session_id: str | None = None,
    conversation_id: str | None = None,
    job_id: str | None = None,
    level: str | None = None,
    logger: str | None = None,
    q: str | None = None,
    since: str | None = None,
    until: str | None = None,
    before: int | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """Central log (newest first). `level` is a minimum, `logger` a prefix, `q` free text,
    `before` a log id for paging back."""
    f = {"user_id": user_id, "session_id": session_id, "conversation_id": conversation_id,
         "job_id": job_id, "level": level, "logger": logger, "q": q, "since": since,
         "until": until, "before": before}  # fmt: skip
    return _s(request).store.list_logs(f, limit)


@router.get("/admin/users", tags=["admin"])
def admin_users(request: Request, admin: Admin) -> list[dict[str, Any]]:
    """Every account with its plan and this month's usage."""
    s = _s(request)
    return [{**u, "usage": usage.summary(s.store, s.settings, u)} for u in s.store.list_users()]


@router.post("/admin/users", status_code=201, tags=["admin"])
def admin_create_user(request: Request, body: UserIn, admin: Admin) -> dict[str, Any]:
    s = _s(request)
    _plan_or_422(request, body.plan)
    if s.store.get_user_by_name(body.username):
        raise HTTPException(409, "Username taken")
    u = s.store.upsert_user(body.username, auth.hash_password(body.password), plan=body.plan,
                            role=body.role)  # fmt: skip
    log.info("user created", extra=fields(target=u["id"], plan=body.plan, role=body.role))
    return s.store.get_user(u["id"])  # type: ignore[return-value]


@router.patch("/admin/users/{uid}", tags=["admin"])
def admin_update_user(request: Request, uid: str, body: UserPatch, admin: Admin) -> dict:
    s = _s(request)
    _plan_or_422(request, body.plan)
    if s.store.get_user(uid) is None:
        raise HTTPException(404, "User not found")
    changes = body.model_dump(exclude_none=True)
    log.info("user updated", extra=fields(target=uid, **changes))
    return s.store.update_user(uid, **changes)  # type: ignore[return-value]
