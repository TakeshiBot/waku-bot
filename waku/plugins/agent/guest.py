"""One AI answer per Telegram Guest query, without ordinary chat tools or history."""

import asyncio
import json
import time
from collections import OrderedDict

from pydantic_ai import Agent
from pydantic_ai.usage import UsageLimits
from pyrogram import Client, enums, raw, types

from waku import database
from waku.common.rich_message import message_plain_text
from waku.config import app_config
from waku.enums import ChatID
from waku.logger import logger
from waku.plugins.agent import provider, quota
from waku.plugins.agent.localization import locale_scope, tr

_seen: OrderedDict[tuple[int, str], float] = OrderedDict()
_active: set[tuple[int, int]] = set()


def _claim(client, query_id):
    now = time.monotonic()
    while _seen and next(iter(_seen.values())) <= now:
        _seen.popitem(last=False)
    key = (id(client), query_id)
    if key in _seen:
        return False
    _seen[key] = now + 900
    while len(_seen) > 4096:
        _seen.popitem(last=False)
    return True


def _clip(text, limit=4096):
    encoded = text.encode("utf-16-le")
    if len(encoded) <= limit * 2:
        return text
    return encoded[: (limit - 1) * 2].decode("utf-16-le", errors="ignore") + "…"


def _frame(message):
    author = message.from_user
    return {
        "sender": author.full_name if author else "",
        "text": _clip(message_plain_text(message), 12000),
        "media": str(message.media) if message.media else None,
    }


def _input(message):
    references = getattr(message, "guest_reference_messages", [])
    if not references and message.reply_to_message:
        references = [message.reply_to_message]
    return json.dumps(
        {
            "request": _frame(message),
            "referenced_messages": [_frame(ref) for ref in references[:8]],
        },
        ensure_ascii=False,
    )


def _make_agent(message, lang):
    group = message.chat and message.chat.type not in (
        enums.ChatType.PRIVATE,
        enums.ChatType.BOT,
    )
    persona = (
        app_config.agent_group_prompt
        if group and app_config.agent_group_prompt
        else app_config.agent_prompt
    )
    return Agent(
        model=provider.make_chat_model(app_config.agent_model),
        model_settings=provider.make_model_settings(app_config.agent_model_options),
        instructions=persona
        + "\n\n"
        + tr("guest.instructions", locale=lang)
        + "\n\n"
        + tr("output_language", locale=lang),
        output_type=str,
        retries=1,
    )


async def _answer(client, message, text):
    if not app_config.telegram_enabled:
        return False
    result = types.InlineQueryResultArticle(
        id=message.guest_query_id,
        title=app_config.nickname or "Waku",
        input_message_content=types.InputTextMessageContent(
            message_text=_clip(text),
            parse_mode=enums.ParseMode.DISABLED,
            disable_web_page_preview=True,
        ),
    )
    await client.invoke(
        raw.functions.messages.SetBotGuestChatResult(
            query_id=int(message.guest_query_id), result=await result.write(client)
        ),
        retries=1,
        sleep_threshold=0,
    )
    return True


@Client.on_guest_message(group=0)
async def guest_chat(client, message):
    if not app_config.telegram_enabled:
        return
    query_id = str(message.guest_query_id or "")
    actor = message.from_user
    if (
        not query_id
        or not actor
        or type(actor.id) is not int
        or actor.id <= 0
        or actor.id
        in (
            int(ChatID.ANONYMOUS_ADMIN),
            int(ChatID.SERVICE_CHAT),
            int(ChatID.FAKE_CHANNEL),
        )
        or actor.is_bot
        or message.sender_chat
        or getattr(message, "business_connection_id", None)
        or not _claim(client, query_id)
    ):
        return
    message.guest_query_id = query_id
    lang = app_config.lang
    key = (id(client), actor.id)
    acquired = False
    attempted = False
    stage = "user"

    async def answer(text):
        nonlocal attempted
        # An uncertain Telegram receipt must not result in a second answer.
        attempted = True
        if await _answer(client, message, text):
            logger.info("Telegram Guest answer delivered")

    try:
        user = await database.upsert_user(actor)
        if user.is_blocked:
            logger.info("Telegram Guest query ignored: blocked caller")
            return
        lang = user.user_config.lang
        with locale_scope(lang):
            if not app_config.agent or not app_config.agent_model:
                await answer(tr("guest.disabled"))
                return
            if key in _active or len(_active) >= 8:
                await answer(tr("guest.busy"))
                return
            _active.add(key)
            acquired = True
            # The calling user's personal quota is shared with ordinary chats.
            # Guest chat IDs can alias normal chats: never charge group credits.
            subject = quota.Subject(user_id=actor.id, chat_id=None, in_group=False)
            stage = "quota"
            if not await quota.can_start(subject):
                await answer(quota.exhausted_text(await quota.get_state(subject), lang))
                return
            stage = "model"
            result = await asyncio.wait_for(
                _make_agent(message, lang).run(
                    _input(message), usage_limits=UsageLimits(request_limit=3)
                ),
                timeout=90,
            )
            # Charge generated tokens even when Telegram cannot deliver the answer.
            stage = "settle"
            await quota.settle(subject, result.usage)
            text = result.output.strip()
            stage = "delivery"
            await answer(text or tr("guest.error"))
    except asyncio.CancelledError:
        raise
    except Exception as error:
        logger.warning(
            "Telegram Guest chat failed at {}: {}", stage, type(error).__name__
        )
        if not attempted:
            try:
                await answer(tr("guest.error", locale=lang))
            except Exception as delivery_error:
                logger.warning(
                    "Telegram guest answer failed: {}", type(delivery_error).__name__
                )
    finally:
        if acquired:
            _active.discard(key)
