"""Lightweight registry for native Telegram drafts and their generation task."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass


@dataclass
class Generation:
    task: asyncio.Task
    connection_id: str | None
    stop: Callable[[], None]


_generations: dict[tuple[int, int, int | None, int], Generation] = {}


def generation_key(client, message, draft_id):
    return (
        id(client),
        message.chat.id,
        getattr(message, "message_thread_id", None),
        draft_id,
    )


def register_generation(client, message, draft_id, stop) -> None:
    task = asyncio.current_task()
    if task is not None:
        _generations[generation_key(client, message, draft_id)] = Generation(
            task, getattr(message, "business_connection_id", None), stop
        )


def unregister_generation(client, message, draft_id) -> None:
    _generations.pop(generation_key(client, message, draft_id), None)


def has_live_generation(client, message) -> bool:
    """Avoid overwriting a draft's typing action with ordinary TYPING."""
    if message.chat is None:
        return False
    prefix = (id(client), message.chat.id, getattr(message, "message_thread_id", None))
    connection = getattr(message, "business_connection_id", None)
    return any(
        key[:3] == prefix and item.connection_id == connection and not item.task.done()
        for key, item in _generations.items()
    )


def stop_generation(client, chat_id, topic_id, draft_id, connection_id=None) -> bool:
    """A stop can affect only the exact active draft, never a whole chat."""
    key = (id(client), chat_id, topic_id, draft_id)
    item = _generations.get(key)
    if item is None or item.task.done():
        return False
    # Native UpdateUserTyping has no connection field. Its unguessable draft
    # ID identifies the already-bound connection. Reject explicit mismatches.
    if connection_id is not None and connection_id != item.connection_id:
        return False
    _generations.pop(key, None)
    item.stop()
    item.task.cancel()
    return True
