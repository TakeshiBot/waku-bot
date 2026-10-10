from __future__ import annotations

import asyncio
import random

import discord
import pydantic_ai
from pydantic_ai import UserContent
from pydantic_ai.messages import (
    ModelMessage,
)

from waku import common
from waku.config import app_config
from waku.logger import logger
from waku.plugins.agent import provider
from waku.plugins.agent.safety import build_usage_limits

from . import state
from .constants import _DISCORD_TEMPORARY_ERROR_REPLIES
from .history import _prepare_discord_history, _sanitize_discord_history
from .messages import _send_reply
from .models import DiscordContextDeps


class DiscordPostRunError(RuntimeError):
    """A run failed after a tool mutation or while delivering/storing its result.

    Retrying the agent here could repeat tool side effects (messages, images,
    reactions or schedules), so callers must not start another model run.
    """


def _discord_model_status_code(error: Exception) -> int | None:
    status = getattr(error, "status_code", None)
    if isinstance(status, int):
        return status
    cause = getattr(error, "__cause__", None)
    cause_status = getattr(cause, "status_code", None)
    return cause_status if isinstance(cause_status, int) else None


def _is_discord_model_error(error: Exception) -> bool:
    return isinstance(
        error,
        (
            pydantic_ai.exceptions.ModelHTTPError,
            pydantic_ai.exceptions.ModelAPIError,
        ),
    )


def _is_discord_temporary_model_error(error: Exception) -> bool:
    status = _discord_model_status_code(error)
    if status in {408, 409, 425, 429, 500, 502, 503, 504}:
        return True
    return isinstance(error, TimeoutError | asyncio.TimeoutError)


def _is_discord_history_error(error: Exception) -> bool:
    status = _discord_model_status_code(error)
    if status != 400 and not isinstance(error, TypeError):
        return False
    text = str(error).casefold()
    history_markers = (
        "tool_call_id",
        "tool call id",
        "tool response",
        "tool result",
        "message history",
        "previous message",
        "role 'tool'",
        'role "tool"',
        "messages[",
        "image_url",
        "content and tool_calls",
    )
    return any(marker in text for marker in history_markers)


def _discord_fallback_reply(error: Exception) -> str:
    if _is_discord_temporary_model_error(error):
        return random.choice(_DISCORD_TEMPORARY_ERROR_REPLIES)
    if _is_discord_history_error(error):
        return "Waku vừa dọn lại ngữ cảnh Discord bị lệch, gọi lại mình lần nữa nha."
    return "Waku xử lý lượt Discord này chưa ổn, thử gọi lại mình sau chút nha."


async def _run_discord_agent_once(
    message: discord.Message,
    prompt: list[UserContent],
    history_key: str,
    message_history: list[ModelMessage],
    model_override,
) -> None:
    assert state.discord_agent is not None
    configured_timeout = app_config.agent_run_timeout
    timeout = configured_timeout if configured_timeout > 0 else 180
    if configured_timeout <= 0:
        logger.warning(
            "agent_run_timeout must be greater than zero; using the safe 180s fallback"
        )
    deps = DiscordContextDeps(message=message)
    options = dict(app_config.agent_model_options)
    if any(not isinstance(item, str) for item in prompt):
        options.update(app_config.agent_model_multimodal_options)

    stream = None
    response_started = asyncio.Event()
    if getattr(app_config, "agent_streaming", False):
        from .streaming import DiscordReplyStream

        stream = DiscordReplyStream(message, deps)

    async def stream_events(ctx, events):
        from pydantic_ai.messages import (
            FunctionToolCallEvent,
            PartDeltaEvent,
            PartStartEvent,
            TextPart,
            TextPartDelta,
            ToolCallPart,
        )

        # Each model response replaces the preview, rather than concatenating
        # intermediate pre-tool narration with the final answer.
        text = ""
        async for event in events:
            if isinstance(event, PartStartEvent) and isinstance(event.part, TextPart):
                text += event.part.content
                if text.strip():
                    response_started.set()
                await stream.update(text)
            elif isinstance(event, PartDeltaEvent) and isinstance(
                event.delta, TextPartDelta
            ):
                text += event.delta.content_delta
                if text.strip():
                    response_started.set()
                await stream.update(text)
            elif (
                isinstance(event, PartStartEvent)
                and isinstance(event.part, ToolCallPart)
            ) or isinstance(event, FunctionToolCallEvent):
                # A tool request is real progress. Preserve the full run budget
                # for tool execution instead of timing out a legitimate action.
                response_started.set()

    async def run_with_history():
        history = await _prepare_discord_history(
            message_history, model_override, deps, state.discord_agent
        )
        stream_options = (
            {"event_stream_handler": stream_events} if stream is not None else {}
        )
        return await state.discord_agent.run(
            user_prompt=prompt,
            message_history=history,
            deps=deps,
            model=model_override,
            model_settings=provider.make_model_settings(options),
            usage_limits=build_usage_limits(),
            **stream_options,
        )

    async def run_with_first_response_deadline():
        first_timeout = getattr(app_config, "agent_model_timeout", 120)
        if stream is None or first_timeout <= 0:
            return await run_with_history()
        run_task = asyncio.create_task(run_with_history())
        started_task = asyncio.create_task(response_started.wait())
        try:
            done, _ = await asyncio.wait(
                (run_task, started_task),
                timeout=first_timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if not done:
                logger.warning(
                    "Discord first response timed out after {}s", first_timeout
                )
                raise TimeoutError("Discord model did not start a response in time")
            return await run_task
        finally:
            for pending in (run_task, started_task):
                if not pending.done():
                    pending.cancel()
            await asyncio.gather(run_task, started_task, return_exceptions=True)

    try:
        async with message.channel.typing():
            result = await asyncio.wait_for(
                run_with_first_response_deadline(),
                timeout=timeout,
            )
    except BaseException as error:
        if stream is not None:
            await stream.abort(
                _discord_fallback_reply(error) if isinstance(error, Exception) else None
            )
        if deps.side_effects_started:
            if not isinstance(error, Exception):
                raise
            raise DiscordPostRunError(
                "Discord run failed after tool side effects"
            ) from error
        raise
    try:
        streamed = (
            await stream.finalize(str(result.output or ""))
            if stream is not None
            else False
        )
        if result.output and not streamed:
            await _send_reply(message, str(result.output))
    except BaseException as error:
        if stream is not None:
            await stream.abort(
                _discord_fallback_reply(error) if isinstance(error, Exception) else None
            )
        if not isinstance(error, Exception):
            raise
        raise DiscordPostRunError("Discord reply delivery failed") from error
    try:
        await common.memttlcache.set(
            history_key,
            _sanitize_discord_history(result.all_messages()),
            ttl=app_config.cachettl_agent_history,
        )
    except Exception as error:
        raise DiscordPostRunError("Discord history persistence failed") from error


async def _discord_recovery_reply(
    message: discord.Message,
    user_prompt: str,
    error: Exception,
) -> str:
    fallback = _discord_fallback_reply(error)
    if state.discord_recovery_agent is None or not _is_discord_model_error(error):
        return fallback
    try:
        status = _discord_model_status_code(error)
        recovery_prompt = (
            "Bạn là Waku trên Discord. Lượt xử lý chính vừa lỗi trước khi trả lời.\n"
            f"Loại lỗi: {error.__class__.__name__}; status={status or 'unknown'}.\n"
            "Nếu có vẻ là quá tải/tạm thời, hãy trả lời tự nhiên bằng tiếng Việt rằng "
            "Waku đang hơi quá tải hoặc nghẽn nhẹ và xin user gọi lại sau chút. "
            "Không nhắc stack trace, provider, API nội bộ, hay tool.\n"
            f"Tin nhắn user: {user_prompt[:1200]}"
        )
        configured_timeout = app_config.agent_small_model_timeout
        timeout = configured_timeout if configured_timeout > 0 else 10
        result = await asyncio.wait_for(
            state.discord_recovery_agent.run(
                user_prompt=recovery_prompt,
                message_history=[],
                model_settings=provider.make_model_settings(
                    app_config.agent_model_small_options
                ),
            ),
            timeout=timeout,
        )
        text = str(result.output or "").strip()
        return text[:1800] if text else fallback
    except Exception as recovery_error:
        logger.debug(
            f"Discord recovery agent failed: {recovery_error.__class__.__name__}"
        )
        return fallback
