"""Persisted event bus. Every event gets a monotonically increasing `seq` so a
client that reconnects (or reloads the page) can replay what it missed."""

import asyncio
from typing import Any

from app.db import Store


class EventBus:
    def __init__(self, store: Store):
        self.store = store
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()

    def publish(
        self,
        type_: str,
        payload: dict[str, Any],
        *,
        conversation_id: str | None = None,
        job_id: str | None = None,
    ) -> dict[str, Any]:
        event = self.store.add_event(type_, payload, conversation_id, job_id)
        for queue in list(self._subscribers):
            queue.put_nowait(event)
        return event

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self._subscribers.discard(queue)
