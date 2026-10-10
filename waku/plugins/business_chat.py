"""Text-only AI replies for connected Telegram Business accounts.

Business updates have their own SDK handlers. They never enter the ordinary
Telegram DM agent, its tools, database preferences or conversation histories.
"""

from __future__ import annotations

import asyncio
import time
import weakref
from collections import OrderedDict
from dataclasses import replace
from typing import TYPE_CHECKING

from pyrogram import Client, enums, filters
from pyrogram.types import BusinessConnection, Message

from waku.common.memory_store import memttlcache
from waku.config import app_config
from waku.logger import logger

if TYPE_CHECKING:
    from pydantic_ai import Agent
    from pydantic_ai.messages import ModelMessage

_chat_locks: weakref.WeakValueDictionary[tuple[str, int], asyncio.Lock] = (
    weakref.WeakValueDictionary()
)
_connection_permissions: dict[str, tuple[bool, int | None]] = {}
_connection_revisions: dict[str, int] = {}
_active_tasks: dict[tuple[str, int], asyncio.Task] = {}
_active_outputs: dict[tuple[str, int], object] = {}
_processed_messages: OrderedDict[tuple[str, int, int], float] = OrderedDict()
_HISTORY_LIMIT = 20
_MAX_PROCESSED_MESSAGES = 4096


def _should_reply(message: Message) -> bool:
    sender = message.from_user
    return bool(
        app_config.business_chat_enabled
        and app_config.agent
        and app_config.agent_model
        and message.business_connection_id
        and message.chat
        and message.chat.type == enums.ChatType.PRIVATE
        and message.chat.id
        and message.id
        and message.text
        and message.text.strip()
        and not message.text.lstrip().startswith("/")
        and not message.outgoing
        and sender
        and not sender.is_bot
    )


def _business_prompt() -> str:
    if (
        app_config.business_chat_prompt_enabled
        and app_config.business_chat_prompt.strip()
    ):
        return app_config.business_chat_prompt
    from waku.plugins.agent.localization import configured_prompt

    return configured_prompt("agent_prompt", app_config.lang)


def _make_business_agent() -> Agent:
    # Keep the plugin importable when agent=false; importing the shared agent
    # stack at plugin discovery time would initialize unrelated Telegram state.
    from pydantic_ai import Agent
    from pydantic_ai_harness.guardrails import OutputGuardrail

    from waku.plugins.agent.provider import make_chat_model, make_model_settings
    from waku.plugins.agent.safety import scrub_output

    assert app_config.agent_model is not None
    return Agent(
        model=make_chat_model(app_config.agent_model),
        model_settings=make_model_settings(app_config.agent_model_options),
        instructions=_business_prompt(),
        output_type=str,
        capabilities=[OutputGuardrail(guard=scrub_output)]
        if app_config.agent_secret_masking
        else [],
    )


async def _compact_business_history(
    history: list[ModelMessage], chat_agent: Agent, usage
):
    from waku.plugins.agent.history import compact_history

    return await compact_history(
        _text_history(history), chat_agent.model, agent=chat_agent, usage=usage
    )


def _text_history(messages: list[ModelMessage]) -> list[ModelMessage]:
    """Keep text dialog, remove old tool/media internals and cap stored history."""
    from pydantic_ai.messages import (
        ModelRequest,
        ModelResponse,
        TextPart,
        UserPromptPart,
    )

    cleaned = []
    for message in messages:
        if isinstance(message, ModelResponse):
            parts = [
                part
                for part in message.parts
                if isinstance(part, TextPart) and part.content.strip()
            ]
        elif isinstance(message, ModelRequest):
            parts = []
            for part in message.parts:
                if isinstance(part, UserPromptPart):
                    text = (
                        part.content
                        if isinstance(part.content, str)
                        else "\n".join(
                            item for item in part.content if isinstance(item, str)
                        )
                    )
                    if text.strip():
                        parts.append(replace(part, content=text))
        else:
            continue
        if parts:
            cleaned.append(replace(message, parts=parts))
    return cleaned[-_HISTORY_LIMIT:]


def _new_streaming_output(client: Client, message: Message, revision: int):
    from waku.plugins.agent.output import StreamingOutput

    return StreamingOutput(
        client, message, should_send=lambda: _still_authorized(message, revision)
    )


async def _send_business_reply(
    client: Client, message: Message, answer: str, revision: int
) -> bool:
    from waku.plugins.agent.output import reply_output

    return await reply_output(
        client,
        message,
        answer,
        should_send=lambda: _still_authorized(message, revision),
    )


def _usage_limits():
    from waku.plugins.agent.safety import build_usage_limits

    return build_usage_limits()


def _new_usage():
    from pydantic_ai.usage import RunUsage

    return RunUsage()


def _remember_connection(connection: BusinessConnection) -> tuple[bool, int | None]:
    owner_id = connection.user.id if connection.user else None
    permission = (
        bool(
            owner_id
            and connection.is_enabled
            and connection.rights
            and connection.rights.can_reply
        ),
        owner_id,
    )
    if _connection_permissions.get(connection.id) != permission:
        _connection_revisions[connection.id] = (
            _connection_revisions.get(connection.id, 0) + 1
        )
    _connection_permissions[connection.id] = permission
    return permission


def _still_authorized(message: Message, revision: int) -> bool:
    connection_id = message.business_connection_id
    can_reply, owner_id = _connection_permissions.get(connection_id, (False, None))
    return bool(
        _should_reply(message)
        and can_reply
        and message.from_user.id != owner_id
        and _connection_revisions.get(connection_id) == revision
    )


async def _abort_output(output) -> None:
    if output is not None:
        try:
            await output.abort()
        except Exception as error:
            logger.debug(f"Business stream cleanup failed: {error.__class__.__name__}")


@Client.on_business_connection(group=0)
async def business_connection_changed(
    client: Client, connection: BusinessConnection
) -> None:
    previous = _connection_permissions.get(connection.id)
    current = _remember_connection(connection)
    if current == previous:
        return
    # Stop background draft/edit tasks too, so revocation cannot leave a stream
    # running after the model coroutine has been cancelled.
    for key, task in list(_active_tasks.items()):
        if key[0] == connection.id:
            task.cancel()
    for key, output in list(_active_outputs.items()):
        if key[0] == connection.id:
            await _abort_output(output)


def _claim_message(message: Message, timeout: float) -> bool:
    now = time.monotonic()
    for key, expires in list(_processed_messages.items()):
        if expires <= now:
            _processed_messages.pop(key, None)
    key = (message.business_connection_id, message.chat.id, message.id)
    if key in _processed_messages:
        return False
    # Claim before model execution. A failed or partially delivered turn must
    # never be rerun because Telegram delivered the same update twice.
    ttl = max(300, timeout, app_config.cachettl_agent_history)
    _processed_messages[key] = now + ttl
    while len(_processed_messages) > _MAX_PROCESSED_MESSAGES:
        _processed_messages.popitem(last=False)
    return True


async def _reply_to_business_message(client: Client, message: Message) -> None:
    if not _should_reply(message):
        return
    connection_id, chat_id = message.business_connection_id, message.chat.id
    try:
        if connection_id not in _connection_permissions:
            connection = await client.get_business_connection(connection_id)
            if connection.id != connection_id:
                return
            # A connection update received during the fetch is authoritative.
            if connection_id not in _connection_permissions:
                _remember_connection(connection)
    except Exception as error:
        logger.warning(f"Business connection lookup failed: {error.__class__.__name__}")
        return

    lock_key = (connection_id, chat_id)
    lock = _chat_locks.get(lock_key)
    if lock is None:
        lock = _chat_locks[lock_key] = asyncio.Lock()
    async with lock:
        revision = _connection_revisions.get(connection_id, 0)
        if not _still_authorized(message, revision):
            return
        timeout = (
            app_config.agent_run_timeout if app_config.agent_run_timeout > 0 else 180
        )
        if not _claim_message(message, timeout):
            return
        task = asyncio.create_task(
            _run_business_turn(client, message, revision, timeout),
            name="business-chat-turn",
        )
        _active_tasks[lock_key] = task
        try:
            await task
        except asyncio.CancelledError:
            # The SDK awaits this callback inside a dispatcher worker. Revoking
            # a connection cancels our child, never that long-lived worker.
            parent = asyncio.current_task()
            if parent is not None and parent.cancelling():
                raise
        finally:
            if _active_tasks.get(lock_key) is task:
                _active_tasks.pop(lock_key, None)


async def _run_business_turn(client, message, revision, timeout):
    lock_key = (message.business_connection_id, message.chat.id)
    output = None
    try:
        async with asyncio.timeout(timeout):
            history_key = f"business_chat_history:{lock_key[0]}:{lock_key[1]}"
            history = await memttlcache.get(history_key, [])
            chat_agent = _make_business_agent()
            usage = _new_usage()
            history = await _compact_business_history(history, chat_agent, usage)
            if not _still_authorized(message, revision):
                return
            if app_config.agent_streaming:
                output = _active_outputs[lock_key] = _new_streaming_output(
                    client, message, revision
                )
                async with chat_agent.run_stream(
                    message.text,
                    message_history=history,
                    usage_limits=_usage_limits(),
                    usage=usage,
                ) as result:
                    async for delta in result.stream_text(delta=True):
                        if not _still_authorized(message, revision):
                            await _abort_output(output)
                            return
                        await output.append_delta(delta)
                    answer = (await result.get_output()).strip()
                    next_history = result.all_messages()
                if not answer or not _still_authorized(message, revision):
                    await _abort_output(output)
                    return
                output.current_text = answer
                if not await output.finalize():
                    return
            else:
                result = await chat_agent.run(
                    message.text,
                    message_history=history,
                    usage_limits=_usage_limits(),
                    usage=usage,
                )
                answer = result.output.strip()
                if not answer or not _still_authorized(message, revision):
                    return
                if not await _send_business_reply(client, message, answer, revision):
                    return
                next_history = result.all_messages()
            if _still_authorized(message, revision):
                await memttlcache.set(
                    history_key,
                    _text_history(next_history),
                    ttl=app_config.cachettl_agent_history,
                )
    except TimeoutError:
        await _abort_output(output)
        logger.warning("Telegram Business chat timed out")
    except asyncio.CancelledError:
        await _abort_output(output)
        raise
    except Exception as error:
        await _abort_output(output)
        logger.warning(f"Telegram Business chat failed: {error.__class__.__name__}")
    finally:
        _active_outputs.pop(lock_key, None)


@Client.on_business_message(filters.private & filters.text, group=0)
async def business_chat_message(client: Client, message: Message) -> None:
    await _reply_to_business_message(client, message)
