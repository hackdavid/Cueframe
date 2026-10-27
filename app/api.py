from typing import Annotated, Any

from fastapi import APIRouter, File, Header, HTTPException, Request, Response, UploadFile
from pydantic import BaseModel, Field

from app import auth, services
from app.agents.contracts import ResearchRequest, ResearchResult
from app.agents.docs_research import DocsResearchAgent
from app.auth import User
from app.llm import LLMError
from app.sandbox.contracts import Action, Answer
from app.uploads import save_upload

router = APIRouter(prefix="/api")


class LoginIn(BaseModel):
    username: str
    password: str


class ConversationIn(BaseModel):
    title: str = "New chat"


class ConversationPatch(BaseModel):
    title: str = Field(min_length=1, max_length=200)


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
@router.post("/auth/login", tags=["auth"])
def login(request: Request, response: Response, body: LoginIn) -> dict[str, Any]:
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
    s.store.update_conversation(cid, title=body.title)
    conv = s.store.get_conversation(cid)
    s.bus.publish("conversation.updated", {"conversation": conv}, conversation_id=cid)
    return conv


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
