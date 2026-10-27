"""WebSocket at /api/ws. Every socket gets a connection id stored in SQLite,
along with the conversation it currently has open.

Client → server frames (JSON, optional `request_id` is echoed back):
  {"type": "open", "conversation_id": "c_…" | null}
  {"type": "message.send", "conversation_id": "c_…" | null, "content": "…",
   "attachment_ids": [], "job_kind": "demo.reply"}
  {"type": "job.cancel", "job_id": "j_…"}
  {"type": "ping"}
Server → client frames:
  {"type": "hello", "connection_id", "user", "last_seq"}
  {"type": "event", "event": {seq, type, conversation_id, job_id, payload, created_at}}
  {"type": "conversation.state" | "ack" | "pong" | "error", "request_id", ...}
"""

import asyncio
import contextlib
from typing import Any

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from app import services
from app.auth import websocket_user

router = APIRouter()


@router.websocket("/api/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    s = ws.app.state
    user = websocket_user(ws)
    if user is None:
        await ws.close(code=4401, reason="Not logged in")
        return
    await ws.accept()
    conn = s.store.open_connection(
        user["id"], ws.query_params.get("client", "ws"), ws.headers.get("user-agent")
    )
    send_lock = asyncio.Lock()

    async def send(frame: dict[str, Any]) -> None:
        async with send_lock:
            await ws.send_json(frame)

    queue = s.bus.subscribe()
    cursor = s.store.last_event_seq()
    after = ws.query_params.get("after")
    await send({"type": "hello", "connection_id": conn["id"], "user": user, "last_seq": cursor})
    if after and after.isdigit():
        for event in s.store.events_after(int(after), user["id"], limit=10_000):
            await send({"type": "event", "event": event})

    async def forward() -> None:
        while True:
            event = await queue.get()
            if event["seq"] > cursor and event["user_id"] == user["id"]:
                await send({"type": "event", "event": event})

    forwarder = asyncio.create_task(forward())
    try:
        while True:
            frame = await ws.receive_json()
            s.store.touch_connection(conn["id"])
            await send(await _handle(s, user, conn["id"], frame))
    except WebSocketDisconnect:
        pass
    finally:
        forwarder.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await forwarder
        s.bus.unsubscribe(queue)
        s.store.close_connection(conn["id"])


async def _handle(s: Any, user: dict[str, Any], conn_id: str, frame: dict[str, Any]) -> dict:
    rid = frame.get("request_id")
    kind = frame.get("type")
    try:
        if kind == "ping":
            return {"type": "pong", "request_id": rid}
        if kind == "open":
            cid = frame.get("conversation_id")
            s.store.touch_connection(conn_id, cid or None)
            state = services.conversation_state(s, cid, user) if cid else None
            return {"type": "conversation.state", "request_id": rid, "conversation": state}
        if kind == "message.send":
            data = services.send_message(
                s,
                user,
                conversation_id=frame.get("conversation_id"),
                content=frame.get("content", ""),
                attachment_ids=frame.get("attachment_ids", []),
                job_kind=frame.get("job_kind", services.DEFAULT_JOB),
                job_input=frame.get("job_input") or {},
                source="ws",
                connection_id=conn_id,
            )
            s.store.touch_connection(conn_id, data["conversation"]["id"])
            return {"type": "ack", "request_id": rid, **data}
        if kind == "job.cancel":
            job = s.store.get_job(frame.get("job_id", ""))
            if not job or not s.store.get_conversation(job["conversation_id"], user["id"]):
                raise HTTPException(404, "Job not found")
            return {"type": "ack", "request_id": rid, "cancelled": s.jobs.cancel(job["id"])}
        if kind == "job.answer":
            job = s.store.get_job(frame.get("job_id", ""))
            if not job or not s.store.get_conversation(job["conversation_id"], user["id"]):
                raise HTTPException(404, "Job not found")
            data = services.answer_job(
                s, job["id"], frame.get("input_id", ""), frame.get("value", "")
            )
            return {"type": "ack", "request_id": rid, **data}
        raise HTTPException(400, f"Unknown frame type: {kind}")
    except HTTPException as exc:
        return {"type": "error", "request_id": rid, "status": exc.status_code, "detail": exc.detail}
