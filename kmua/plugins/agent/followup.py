import asyncio

import pyrogram
from pydantic import BaseModel, Field
from pydantic_ai import Agent, ModelMessage
from pyrogram.client import Client as PyrogramClient

from kmua import common, database, enums
from kmua.common.memory_store import memttlcache
from kmua.common.utils import GROUP_CHAT_TYPES, is_explicit_reply
from kmua.config import app_config
from kmua.logger import logger
from kmua.plugins.agent import datatype, jev, provider, quota, state, trace
from kmua.plugins.agent.localization import localized_message, tr
from kmua.plugins.agent.prompt import build_ctx_info, get_input_prompt
from kmua.plugins.agent.runner import (
    get_chat_model_override,
    run_agent,
)

from .agent import (
    _queue_interjection,
    _run_registered,
    agent,
    model,
    multimodal_model,
    powermemory,
    small_model,
)
from .tools import block as tools
from .whitelist import is_chat_allowed


class RelevanceCheck(BaseModel):
    relevance: bool = Field(description=tr("is_relevant"))
    reason: str = Field(description=tr("relevance_reason"))


if small_model:
    _default_relevance_check_agent = Agent(
        model=small_model or model,
        model_settings=provider.make_model_settings(
            app_config.agent_model_small_options
        ),
        output_type=RelevanceCheck,
        system_prompt=tr("relevance_system"),
        capabilities=[trace.AgentTraceCapability()],
        retries=2,
    )
else:
    _default_relevance_check_agent = None


def _make_relevance_check_agent(
    override_model_spec: str | None,
) -> Agent[None, RelevanceCheck] | None:
    """Relevance-check agent using the per-chat small model override when set,
    else the module-level default (which uses the global small_model)."""
    if override_model_spec:
        return Agent(
            model=provider.make_chat_model(override_model_spec),
            model_settings=provider.make_model_settings(
                app_config.agent_model_small_options
            ),
            output_type=RelevanceCheck,
            system_prompt=tr("relevance_system"),
            capabilities=[trace.AgentTraceCapability()],
            retries=2,
        )
    return _default_relevance_check_agent


async def _follow_up_filter_func(
    _, client: PyrogramClient, message: pyrogram.types.Message
) -> bool:
    if not app_config.agent or not app_config.agent_follow_up:
        return False
    if not _default_relevance_check_agent and not app_config.agent_followup_jev_model:
        return False
    if not message or not message.chat:
        return False
    chat = message.chat
    if chat.type not in GROUP_CHAT_TYPES:
        return False
    if not chat.id:
        return False
    if not is_chat_allowed(chat.id):
        return False
    text = message.text or message.caption
    if not text or len(text.strip()) == 0:
        return False
    if (
        message.entities is not None
        and message.entities[0].type == pyrogram.enums.MessageEntityType.BOT_COMMAND
    ):
        return False
    if text.startswith("/") or text.startswith("\\"):
        return False
    user = message.sender_chat or message.from_user
    if (
        not user
        or not user.id
        or message.outgoing
        or message.service
        or message.automatic_forward
        or is_explicit_reply(message)  # Skip messages already explicitly replied to by the user.
        or (
            user.id
            in (
                enums.ChatID.ANONYMOUS_ADMIN,
                enums.ChatID.SERVICE_CHAT,
                enums.ChatID.FAKE_CHANNEL,
            )
        )
    ):
        return False
    # Explicit @ mentions do not need follow-up processing.
    if message.entities:
        has_mention = any(
            entity.type == pyrogram.enums.MessageEntityType.MENTION
            or entity.type == pyrogram.enums.MessageEntityType.TEXT_MENTION
            for entity in message.entities
        )
        if has_mention:
            return False
    if chat is not None and chat.id is not None and state.is_running(chat.id, user.id):
        # Mid-turn: the message is an interjection, not a follow-up trigger.
        # Deliver it straight into the live run (budget-checked), so the
        # running turn picks it up on its next model request.
        text = (message.text or message.caption or "").strip()
        if not text:
            return False
        if app_config.nickname and app_config.nickname in text:
            return False
        if not state.enqueue_interjection(chat.id, user.id, text):
            logger.warning(
                f"Follow-up interjection dropped for user {user.id} in "
                f"chat {chat.id}: interjection budget exhausted"
            )
        return False
    return True


follow_up_filter = pyrogram.filters.create(_follow_up_filter_func)


@PyrogramClient.on_message(follow_up_filter, group=10)
@localized_message(prefer_chat=True)
async def handle_follow_up_message(
    client: PyrogramClient, message: pyrogram.types.Message
):
    if not app_config.agent or agent is None or model is None:
        return
    chat = message.chat
    user = message.sender_chat or message.from_user
    assert chat is not None and chat.id is not None, "Invalid chat in follow-up"
    assert user is not None and user.id is not None, "Invalid user in follow-up"
    if not is_chat_allowed(chat.id):
        return
    if await tools.is_user_blocked(user.id):
        return
    if await common.memttlcache.get(
        state.message_follow_up_lock_key(chat.id, message.id)
    ):
        return
    await common.memttlcache.set(
        state.message_follow_up_lock_key(chat.id, message.id), True, ttl=60
    )
    bot_reply = await common.memttlcache.get(state.bot_last_reply_key(chat.id))
    if not bot_reply or not isinstance(bot_reply, datatype.BotLastReply):
        return
    if not message.date:
        return
    time_diff = message.date.timestamp() - bot_reply.timestamp
    if time_diff < 0 or time_diff > 300:
        return
    if message.id - bot_reply.message_id > 3:
        return
    chat_config = await database.get_chat_config(chat.id)
    if not chat_config.ai_reply:
        return
    subject = quota.subject_of(message)
    if not await quota.can_start(subject):
        # Skip relevance checks without quota; the check itself makes a model call.
        await trace.note_rejection(
            "followup",
            chat_id=chat.id,
            user_id=user.id,
            message_id=message.id,
            reason="quota",
        )
        return
    user_data = await database.get_user_by_id(user.id)
    if not user_data:
        return
    reply_to_user = await database.get_user_by_id(bot_reply.reply_to_user_id)
    if not reply_to_user:
        return
    message_text = message.text or message.caption
    # Use full_output (the complete model output), since reply_text may contain only the final message.
    bot_full_output = (
        bot_reply.full_output if bot_reply.full_output else bot_reply.reply_text
    )
    relevance_check_prompt = tr(
        "followup_check",
        p0=bot_reply.original_user_message,
        p1=bot_full_output,
        p2=message_text,
    )
    session: trace.TraceSession | None = None
    try:
        # Apply the small-model timeout so relevance checks cannot stall the event loop.
        timeout = app_config.agent_small_model_timeout
        jev_model_spec = app_config.agent_followup_jev_model
        if jev_model_spec:
            # Experimental: jev returns probabilities, not text, so bypass pydantic-ai.
            coro = jev.check_relevance(
                relevance_check_prompt,
                spec=jev_model_spec,
                threshold=app_config.agent_followup_jev_threshold,
                timeout=timeout if timeout > 0 else None,
            )
        else:
            small_model_override = await get_chat_model_override(chat.id, "small")
            relevance_check_agent = _make_relevance_check_agent(small_model_override)
            if not relevance_check_agent:
                return
            coro = relevance_check_agent.run(
                user_prompt=relevance_check_prompt,
            )

        session = await trace.start_trace(
            "followup_relevance",
            chat_id=chat.id,
            user_id=user.id,
            message_id=message.id,
            model_role="small",
        )
        if jev_model_spec:
            trace.mark_trace(session, model_name=jev_model_spec)

        if timeout > 0:
            try:
                relevance_result = await asyncio.wait_for(coro, timeout=timeout)
            except TimeoutError as e:
                trace.mark_trace(session, status="timeout", error=e)
                logger.warning(f"Follow-up relevance check timed out after {timeout}s")
                return
        else:
            relevance_result = await coro

        # Settle the relevance-check model call immediately, including results judged irrelevant;
        # its tokens have already been spent and must appear in the panel.
        await quota.settle(subject, relevance_result.usage)
        trace.mark_trace(
            session,
            usage=relevance_result.usage,
            output=(
                f"relevance={relevance_result.output.relevance} "  # type: ignore[union-attr]
                f"reason={relevance_result.output.reason}"  # type: ignore[union-attr]
            ),
        )
        if not relevance_result.output.relevance:  # type: ignore[union-attr]
            return
    except Exception as e:
        trace.mark_trace(session, status="error", error=e)
        logger.error(
            f"Error checking follow-up relevance: {e.__class__.__name__} - {e}"
        )
        return
    finally:
        trace.finish_trace(session)
    logger.info(
        f"Detected follow-up message {message.id} (reason: {relevance_result.output.reason})"  # type: ignore[union-attr]
    )
    follow_lock = state.get_conversation_lock(chat.id, user.id)
    if follow_lock.locked():
        await _queue_interjection(message, chat.id, user.id)
        return
    await follow_lock.acquire()
    try:
        await message.reply_chat_action(pyrogram.enums.ChatAction.TYPING)
        instructions = (
            app_config.agent_group_prompt
            if app_config.agent_group_prompt
            else app_config.agent_prompt
        )
        chat_prompt = await memttlcache.get(state.chat_prompt_override_key(chat.id))
        if chat_prompt:
            instructions = chat_prompt
        history: list[ModelMessage] = await memttlcache.get(
            state.history_key(chat.id, user.id), []
        )
        ctx_info = await build_ctx_info(
            message=message,
            user=user,
            user_data=user_data,
            history=history,
            is_group_chat=chat.type in GROUP_CHAT_TYPES,
        )
        follow_up_prompt, _, _ = await get_input_prompt(
            client, message, include_nearby=0, ctx=None
        )
        addtional_instructions = ctx_info.to_text() if ctx_info else ""
        # Use full_output (the complete model output) rather than reply_text.
        bot_full_output = (
            bot_reply.full_output if bot_reply.full_output else bot_reply.reply_text
        )
        addtional_instructions += tr(
            "followup_context",
            p0=reply_to_user.full_name,
            p1=bot_reply.original_user_message,
            p2=bot_full_output,
            p3=user_data.full_name,
        )
        await _run_registered(
            chat.id,
            user.id,
            run_agent(
                agi=agent,
                additional_instructions=addtional_instructions,
                client=client,
                message=message,
                user_id=user.id,
                chat_id=chat.id,
                user_prompt=follow_up_prompt,
                history=history,
                deps=datatype.ContextDeps(
                    locale=chat_config.lang,
                    user_id=user.id,
                    chat_id=chat.id,
                    message=message,
                    client=client,
                    instructions=instructions,
                    powermemory=powermemory,
                    history=history,
                ),
                multimodal_model=multimodal_model,
                model=model,
                lang=chat_config.lang,
                subject=subject,
                trace_kind="followup",
                coverage_meta=state.PromptCoverage(last_message_id=message.id),
            ),
        )

    except Exception as e:
        logger.exception(
            f"Error handling follow-up message: {e.__class__.__name__} - {e}"
        )
    finally:
        follow_lock.release()
