"""Operations shared by the REST API and the WebSocket, so every client
(browser, Postman, MCP) drives the agent through the same code path."""

from typing import Any

from fastapi import HTTPException

DEFAULT_JOB = "assistant.reply"


def conversation_or_404(s: Any, cid: str, user: dict[str, Any]) -> dict[str, Any]:
    conv = s.store.get_conversation(cid, user["id"])
    if conv is None:
        raise HTTPException(404, "Conversation not found")
    return conv


def create_conversation(s: Any, user: dict[str, Any], title: str = "New chat") -> dict[str, Any]:
    conv = s.store.create_conversation(user["id"], title)
    s.bus.publish("conversation.created", {"conversation": conv}, conversation_id=conv["id"])
    return conv


def conversation_state(s: Any, cid: str, user: dict[str, Any]) -> dict[str, Any]:
    """Snapshot used when a client opens or refreshes a conversation: messages,
    job history, which jobs are still pending, and who is connected."""
    conv = conversation_or_404(s, cid, user)
    jobs = s.store.list_jobs(cid)
    active = [j for j in jobs if j["status"] in ("queued", "running", "waiting_input")]
    for j in active:
        sb = s.store.get_sandbox(j["sandbox_id"]) if j.get("sandbox_id") else None
        if sb and sb["status"] == "running":
            j["live_url"] = sb["live_url"]
    return {
        **conv,
        "messages": s.store.list_messages(cid),
        "jobs": jobs,
        "active_jobs": active,
        "has_pending_job": bool(active),
        "connections": s.store.list_connections(user["id"], cid),
        "last_seq": s.store.last_event_seq(),
    }


def send_message(
    s: Any,
    user: dict[str, Any],
    *,
    conversation_id: str | None,
    content: str,
    attachment_ids: list[str],
    job_kind: str = DEFAULT_JOB,
    job_input: dict[str, Any] | None = None,
    source: str = "api",
    connection_id: str | None = None,
) -> dict[str, Any]:
    """Store the user's message and start the background job that answers it.
    Creates a conversation when `conversation_id` is None."""
    if not content.strip() and not attachment_ids:
        raise HTTPException(422, "Message needs text or an attachment")
    attachments = s.store.get_uploads(attachment_ids, user["id"])
    if len(attachments) != len(set(attachment_ids)):
        raise HTTPException(422, "Unknown attachment id")
    conv = (
        conversation_or_404(s, conversation_id, user)
        if conversation_id
        else create_conversation(s, user)
    )
    cid = conv["id"]
    message = s.store.add_message(
        cid, "user", content, attachments, source=source, connection_id=connection_id
    )
    s.bus.publish("message.created", {"message": message}, conversation_id=cid)
    if conv["title"] == "New chat":
        s.store.update_conversation(cid, title=(content.strip() or attachments[0]["filename"])[:60])
        conv = s.store.get_conversation(cid)
        s.bus.publish("conversation.updated", {"conversation": conv}, conversation_id=cid)
    try:
        job = s.jobs.submit(
            job_kind,
            {"text": content, "attachments": attachments, "job_input": job_input or {}},
            cid,
            connection_id,
        )
    except KeyError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"conversation": conv, "message": message, "job": job}


def answer_job(s: Any, job_id: str, input_id: str, value: str) -> dict[str, Any]:
    try:
        s.jobs.answer(job_id, input_id, value)
    except LookupError as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"answered": True, "job_id": job_id, "input_id": input_id}
