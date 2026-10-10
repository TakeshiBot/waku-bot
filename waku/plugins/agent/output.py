import asyncio
import secrets
from collections.abc import Callable
from datetime import datetime

import pyrogram
import pyrogram.errors
from pyrogram.client import Client as PyrogramClient

from waku.common.memory_store import memttlcache
from waku.common.rich_message import (
    message_plain_text,
    rich_html_plain_text,
    send_rich_message,
)
from waku.common.utils import GROUP_CHAT_TYPES
from waku.config import app_config
from waku.logger import logger
from waku.plugins.agent import datatype, generation, state
from waku.plugins.agent.rich_output import (
    OfficialRichDraftStreamer,
    bind_business_message,
    send_generation_message,
)
from waku.plugins.agent.styling import (
    convert_md,
    convert_md_chunks,
    convert_rich_md,
    split_plain_text,
)


class DeliveryUncertain(RuntimeError):
    """A send may be delivered, or only a prefix was delivered; halt the turn."""


_FORMAT_REJECTIONS = (
    pyrogram.errors.RichMessageUnsupported,
    pyrogram.errors.EntityBoundsInvalid,
    pyrogram.errors.EntitiesTooLong,
    pyrogram.errors.MessageTooLong,
)

# A rich send that keeps failing (unsupported client, server-side outage) is
# paused for a while: every attempt otherwise costs a failed request before
# the plain fallback.
_RICH_FAILURE_LIMIT = 3
_RICH_FAILURE_TTL = 300
_RICH_FAILURE_KEY = "agent_rich_failures"
_RICH_DISABLED_KEY = "agent_rich_disabled"


def _text_key(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n").strip()


def text_already_sent(
    deps: datatype.ContextDeps, text: str, *, markdown: bool = True
) -> bool:
    """Compare a proposed Markdown reply with text delivered during this run."""
    if not deps.sent_texts:
        return False
    if _text_key(text) in deps.sent_texts:
        return True
    if markdown:
        plain, _ = convert_md(text)
        return _text_key(plain) in deps.sent_texts
    return False


def record_sent_text(
    deps: datatype.ContextDeps, text: str, *, markdown: bool = False
) -> None:
    """Call only after delivery succeeds; retain raw and rendered Markdown keys."""
    key = _text_key(text)
    if key:
        deps.sent_texts.add(key)
    if markdown:
        plain, _ = convert_md(text)
        key = _text_key(plain)
        if key:
            deps.sent_texts.add(key)


async def record_tool_reply(
    deps: datatype.ContextDeps, result: object, text: str
) -> None:
    """Keep tool-only replies available to the existing group follow-up detector."""
    record_sent_text(deps, text)
    delivered_text = getattr(result, "text", None)
    if isinstance(delivered_text, str):
        record_sent_text(deps, delivered_text)
    message = deps.message
    chat = message.chat
    user = message.sender_chat or message.from_user
    message_id = getattr(result, "id", None) or getattr(result, "message_id", None)
    if not (message_id and chat and chat.type in GROUP_CHAT_TYPES and user and user.id):
        return
    bot_reply = datatype.BotLastReply(
        message_id=message_id,
        reply_to_user_id=user.id,
        reply_to_message_id=message.id,
        reply_text=delivered_text if isinstance(delivered_text, str) else text,
        original_user_message=message_plain_text(message),
        moderation_reference=getattr(deps, "moderation_reference", None),
        timestamp=datetime.now().timestamp(),
    )
    try:
        await memttlcache.set(state.bot_last_reply_key(chat.id), bot_reply, ttl=300)
    except Exception as e:
        # A cache failure must not turn an already-delivered send into a retry.
        logger.debug(f"Failed to cache tool reply: {e.__class__.__name__} - {e}")


async def _rich_output_enabled(message=None) -> bool:
    if not app_config.agent_rich_output:
        return False
    connection_id = getattr(message, "business_connection_id", None)
    suffix = f":business:{connection_id}" if connection_id else ""
    return not await memttlcache.get(_RICH_DISABLED_KEY + suffix, False)


async def _note_rich_result(sent: bool, message=None) -> None:
    connection_id = getattr(message, "business_connection_id", None)
    suffix = f":business:{connection_id}" if connection_id else ""
    failure_key = _RICH_FAILURE_KEY + suffix
    disabled_key = _RICH_DISABLED_KEY + suffix
    try:
        if sent:
            await memttlcache.delete(failure_key)
            await memttlcache.delete(disabled_key)
            return
        failures = int(await memttlcache.get(failure_key, 0) or 0) + 1
        await memttlcache.set(failure_key, failures, ttl=_RICH_FAILURE_TTL)
        if failures >= _RICH_FAILURE_LIMIT:
            logger.warning(
                f"Rich message output paused for {_RICH_FAILURE_TTL}s after "
                f"{failures} consecutive send failures"
            )
            await memttlcache.set(disabled_key, True, ttl=_RICH_FAILURE_TTL)
    except Exception as error:
        logger.warning(f"Rich circuit cache failed: {type(error).__name__}")


async def _send_rich_payloads(
    client: PyrogramClient,
    message: pyrogram.types.Message,
    payloads: list[pyrogram.types.InputRichMessage],
    should_send: Callable[[], bool] | None = None,
) -> tuple[int, int | None]:
    """Send rich payloads in order, stopping at the first failure.

    Returns (delivered count, last delivered message id); callers deliver the
    undelivered tail through the plain text path.
    """
    chat = message.chat
    if not payloads or chat is None or chat.id is None:
        return 0, None
    chat_id = chat.id
    last_id: int | None = None
    connection_id = getattr(message, "business_connection_id", None)
    business_kwargs = {"business_connection_id": connection_id} if connection_id else {}
    for index, payload in enumerate(payloads):
        if should_send is not None and not should_send():
            return index, last_id
        try:
            last_id = await send_rich_message(
                client,
                chat_id,
                payload.write(),
                reply_parameters=pyrogram.types.ReplyParameters(message_id=message.id),
                message_thread_id=message.message_thread_id,
                direct_messages_topic_id=message.direct_messages_topic_id,
                **business_kwargs,
            )
            if last_id is None:
                raise DeliveryUncertain("Missing rich delivery receipt")
        except _FORMAT_REJECTIONS as e:
            logger.warning(f"Rich format rejected: {e.__class__.__name__}")
            await _note_rich_result(False, message)
            return index, last_id
        except pyrogram.errors.RPCError as e:
            if index:
                raise DeliveryUncertain("Partial rich delivery") from e
            raise
        except Exception as e:
            raise DeliveryUncertain("Unconfirmed rich delivery") from e
    await _note_rich_result(True, message)
    return len(payloads), last_id


async def _send_rich_tail_plain(
    message: pyrogram.types.Message,
    payloads: list[pyrogram.types.InputRichMessage],
    should_send: Callable[[], bool] | None = None,
) -> bool:
    text = "\n\n".join(
        part
        for part in (
            rich_html_plain_text(payload.html)
            if payload.html
            else convert_md(payload.markdown or "")[0]
            for payload in payloads
        )
        if part
    )
    if not text:
        return True
    for chunk in split_plain_text(text):
        if should_send is not None and not should_send():
            return False
        try:
            result = await message.reply_text(
                chunk, parse_mode=pyrogram.enums.ParseMode.DISABLED
            )
            if result is None:
                raise DeliveryUncertain("Missing rich fallback receipt")
            if getattr(message, "business_connection_id", None):
                bind_business_message(message, result)
        except pyrogram.errors.RPCError as e:
            logger.error(f"Rich fallback rejected: {e.__class__.__name__}")
            return False
        except Exception as e:
            raise DeliveryUncertain("Unconfirmed rich fallback delivery") from e
    return True


async def _send_plain_reply(
    message: pyrogram.types.Message,
    markdown: str,
    should_send: Callable[[], bool] | None = None,
) -> pyrogram.types.Message | None:
    """Send markdown as plain text + entities.

    Fallback is permitted only after an explicit format rejection. Any
    unconfirmed send stops the turn; subsequent chunks are not attempted.
    """
    last_msg: pyrogram.types.Message | None = None
    for plain, entities in convert_md_chunks(markdown):
        if should_send is not None and not should_send():
            return last_msg
        try:
            last_msg = await message.reply_text(
                plain, entities=entities, parse_mode=pyrogram.enums.ParseMode.DISABLED
            )
            if last_msg is None:
                raise DeliveryUncertain("Missing delivery receipt")
            if getattr(message, "business_connection_id", None):
                bind_business_message(message, last_msg)
        except _FORMAT_REJECTIONS as e:
            logger.warning(f"Plain format rejected: {e.__class__.__name__}")
            if should_send is not None and not should_send():
                return last_msg
            try:
                last_msg = await message.reply_text(
                    plain, parse_mode=pyrogram.enums.ParseMode.DISABLED
                )
                if last_msg is None:
                    raise DeliveryUncertain("Missing fallback delivery receipt")
                if getattr(message, "business_connection_id", None):
                    bind_business_message(message, last_msg)
            except pyrogram.errors.RPCError as e:
                if last_msg is not None:
                    raise DeliveryUncertain("Partial plain delivery") from e
                raise
            except Exception as e:
                raise DeliveryUncertain("Unconfirmed plain fallback delivery") from e
        except pyrogram.errors.RPCError as e:
            if last_msg is not None:
                raise DeliveryUncertain("Partial plain delivery") from e
            raise
        except Exception as e:
            raise DeliveryUncertain("Unconfirmed plain delivery") from e
    return last_msg


async def reply_output(
    client: PyrogramClient,
    message: pyrogram.types.Message,
    text: str,
    should_send: Callable[[], bool] | None = None,
    *,
    deps: datatype.ContextDeps | None = None,
) -> bool:
    if should_send is not None and not should_send():
        return False
    if message.chat is None:
        return False
    is_group_chat = message.chat.type in GROUP_CHAT_TYPES
    user = message.sender_chat or message.from_user
    if not text.strip():
        return False
    try:
        last_reply_id: int | None = None
        last_reply_msg: pyrogram.types.Message | None = None
        last_reply_text = ""
        sent_count = 0
        if await _rich_output_enabled(message):
            payloads = convert_rich_md(text)
            sent_count, last_reply_id = await _send_rich_payloads(
                client, message, payloads, should_send=should_send
            )
            if sent_count:
                last_reply_text = text
            if 0 < sent_count < len(payloads):
                if not await _send_rich_tail_plain(
                    message, payloads[sent_count:], should_send=should_send
                ):
                    raise DeliveryUncertain("Partial rich delivery")
        if not last_reply_text:
            last_reply_msg = await _send_plain_reply(
                message, text, should_send=should_send
            )
            last_reply_text = text
        if should_send is not None and not should_send():
            return False
        last_reply_message_id = last_reply_id or (
            last_reply_msg.id if last_reply_msg else None
        )
        if (
            last_reply_message_id
            and last_reply_text
            and is_group_chat
            and user
            and user.id
        ):
            bot_reply = datatype.BotLastReply(
                message_id=last_reply_message_id,
                reply_to_user_id=user.id,
                reply_to_message_id=message.id,
                reply_text=last_reply_text,
                original_user_message=message_plain_text(message),
                moderation_reference=getattr(deps, "moderation_reference", None),
                timestamp=datetime.now().timestamp(),
            )
            _chat = message.chat
            _chat_id = _chat.id if _chat else None
            if _chat_id:
                try:
                    await memttlcache.set(
                        state.bot_last_reply_key(_chat_id), bot_reply, ttl=300
                    )
                except Exception as error:
                    logger.warning(
                        f"Reply context cache failed: {type(error).__name__}"
                    )
        return bool(last_reply_id or last_reply_msg or sent_count)
    except DeliveryUncertain:
        raise
    except Exception as e:
        logger.error(f"Error replying message: {e.__class__.__name__} - {e}")
        return False


class TypingKeepAlive:
    """Standalone context manager that keeps the TYPING action going
    independently of StreamingOutput, so typing continues during tool calls too.
    """

    CHAT_ACTION_INTERVAL = 4

    def __init__(self, client: PyrogramClient, message: pyrogram.types.Message):
        self.client = client
        self.message = message
        self._stop = False
        self._task: asyncio.Task | None = None

    async def _loop(self):
        chat = self.message.chat
        chat_id = chat.id if chat else None
        if not chat_id:
            return
        first = True
        while not self._stop:
            try:
                if not first:
                    await asyncio.sleep(self.CHAT_ACTION_INTERVAL)
                    if self._stop:
                        break
                first = False
                if generation.has_live_generation(self.client, self.message):
                    continue
                topic = getattr(self.message, "message_thread_id", None)
                if topic is not None:
                    await self.client.invoke(
                        pyrogram.raw.functions.messages.SetTyping(
                            peer=await self.client.resolve_peer(chat_id),
                            action=pyrogram.raw.types.SendMessageTypingAction(),
                            top_msg_id=topic,
                        ),
                        business_connection_id=getattr(
                            self.message, "business_connection_id", None
                        ),
                    )
                    continue
                await self.client.send_chat_action(
                    chat_id=chat_id,
                    action=pyrogram.enums.ChatAction.TYPING,
                    business_connection_id=getattr(
                        self.message, "business_connection_id", None
                    ),
                )
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug(f"TypingKeepAlive: error sending chat action: {e}")
                break

    def start(self):
        self._stop = False
        self._task = asyncio.create_task(self._loop())

    async def stop(self):
        self._stop = True
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def __aenter__(self):
        self.start()
        return self

    async def __aexit__(self, *_):
        await self.stop()


class StreamingOutput:
    """Native ephemeral drafts plus one persistent final response.

    Unsupported peers and group chats buffer text without sending/editing a
    preview. Long final responses still split at Telegram's payload limits.
    """

    MAX_MESSAGE_LENGTH = 4000
    MAX_TOTAL_TIME = float(app_config.agent_streaming_max_time)

    def __init__(self, client, message, should_send=None, *, deps=None):
        self.client = client
        self.message = message
        self.deps = deps
        self.should_send = should_send
        self.current_text = ""
        self.reply_message_id = None
        self.delivered = False
        self.is_group_chat = (
            message.chat is not None and message.chat.type in GROUP_CHAT_TYPES
        )
        self.user = message.sender_chat or message.from_user
        self._stop = False
        self.official_draft = (
            OfficialRichDraftStreamer(
                client,
                message,
                should_send=should_send,
                max_duration=self.MAX_TOTAL_TIME,
            )
            if message.chat and message.chat.type == pyrogram.enums.ChatType.PRIVATE
            else None
        )
        self.random_id = (
            self.official_draft.random_id
            if self.official_draft
            else (secrets.randbits(63) or 1)
        )
        self._draft_attempted = False
        self._registered = False
        self._cancelled = False
        self._finalized = False
        self._completion_success = False

    def request_stop(self):
        """Called synchronously before cancelling the model generation task."""
        self._cancelled = True
        self._stop = True
        if self.official_draft is not None:
            self.official_draft._stop = True
            if self.official_draft._task is not None:
                self.official_draft._task.cancel()

    def _may_send(self):
        return not self._cancelled and (self.should_send is None or self.should_send())

    def _unregister(self):
        if self._registered:
            generation.unregister_generation(self.client, self.message, self.random_id)
            self._registered = False

    async def append_delta(self, delta: str):
        if not delta or self._finalized or not self._may_send():
            return
        self.current_text += delta
        if self.official_draft is None:
            # Telegram does not support native drafts in groups. Buffer the
            # answer and persist it once instead of repeatedly editing it.
            return
        self.official_draft.update(self.current_text)
        if self._draft_attempted:
            return
        self._draft_attempted = True
        self.official_draft.rich = await _rich_output_enabled(self.message)
        generation.register_generation(
            self.client, self.message, self.random_id, self.request_stop
        )
        self._registered = True
        if not await self.official_draft.start():
            self._unregister()

    async def _finalize_generation(self):
        if self._finalized:
            return self._completion_success
        self._finalized = True
        try:
            if self.official_draft is not None:
                await self.official_draft.stop()
            if not self.current_text.strip() or not self._may_send():
                return False
            payloads = (
                convert_rich_md(self.current_text)
                if await _rich_output_enabled(self.message)
                else []
            )
            if payloads:
                chunks = [(None, payload) for payload in payloads]
            else:
                chunks = [
                    (chunk, None)
                    for chunk in convert_md_chunks(
                        self.current_text, self.MAX_MESSAGE_LENGTH
                    )
                ]
            for index, (plain_chunk, payload) in enumerate(chunks):
                if not self._may_send():
                    return False
                chunk_id = self.random_id if index == 0 else (secrets.randbits(63) or 1)
                try:
                    sent_id = await send_generation_message(
                        self.client,
                        self.message,
                        chunk_id,
                        text=plain_chunk[0] if plain_chunk else "",
                        entities=plain_chunk[1] if plain_chunk else None,
                        rich_message=payload,
                        should_send=self._may_send,
                    )
                except (
                    pyrogram.errors.RichMessageUnsupported,
                    pyrogram.errors.EntityBoundsInvalid,
                    pyrogram.errors.EntitiesTooLong,
                ) as error:
                    # These RPC errors explicitly reject delivery. Retry only
                    # the rejected chunk; an unknown transport error might
                    # already have delivered it and must never cause a resend.
                    if payload is not None:
                        original = getattr(payload, "markdown", None)
                        plain = (
                            convert_md_chunks(original, self.MAX_MESSAGE_LENGTH)
                            if original
                            else [
                                (part, [])
                                for part in split_plain_text(
                                    rich_html_plain_text(payload.html or ""),
                                    self.MAX_MESSAGE_LENGTH,
                                )
                            ]
                        )
                    else:
                        plain = [(plain_chunk[0], [])]
                    sent_id = None
                    for tail_index, (text, entities) in enumerate(plain):
                        sent_id = await send_generation_message(
                            self.client,
                            self.message,
                            chunk_id
                            if tail_index == 0
                            else (secrets.randbits(63) or 1),
                            text=text,
                            entities=entities,
                            should_send=self._may_send,
                        )
                        if sent_id is None:
                            if not self._may_send():
                                return False
                            raise DeliveryUncertain(
                                "Missing generation fallback receipt"
                            )
                        self.delivered = True
                        self.reply_message_id = sent_id
                    logger.debug(f"Generation format fallback: {type(error).__name__}")
                if sent_id is None:
                    if not self._may_send():
                        return False
                    raise DeliveryUncertain("Missing generation delivery receipt")
                self.delivered = True
                self.reply_message_id = sent_id
            if self.reply_message_id and self.is_group_chat and self.user:
                try:
                    await memttlcache.set(
                        state.bot_last_reply_key(self.message.chat.id),
                        datatype.BotLastReply(
                            message_id=self.reply_message_id,
                            reply_to_user_id=self.user.id,
                            reply_to_message_id=self.message.id,
                            reply_text=self.current_text,
                            original_user_message=message_plain_text(self.message),
                            moderation_reference=getattr(
                                self.deps, "moderation_reference", None
                            ),
                            timestamp=datetime.now().timestamp(),
                        ),
                        ttl=300,
                    )
                except Exception as error:
                    logger.warning(
                        f"Reply context cache failed: {type(error).__name__}"
                    )
            self._completion_success = True
            return True
        except pyrogram.errors.RPCError as error:
            if self.delivered:
                raise DeliveryUncertain("Partial generation delivery") from error
            logger.error(f"Generation final send failed: {type(error).__name__}")
            return False
        except Exception as error:
            raise DeliveryUncertain("Unconfirmed generation delivery") from error
        finally:
            self._unregister()

    async def finalize(self) -> bool:
        return await self._finalize_generation()

    async def abort(self):
        self.request_stop()
        self._unregister()
        if self.official_draft is not None:
            await self.official_draft.stop()
