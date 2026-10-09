import asyncio
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
from waku.plugins.agent import datatype, state
from waku.plugins.agent.rich_output import (
    OfficialRichDraftStreamer,
    bind_business_message,
    edit_business_message_text,
)
from waku.plugins.agent.styling import (
    convert_md,
    convert_md_chunks,
    convert_rich_md,
    split_plain_text,
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
        except Exception as e:
            logger.warning(f"Rich message send failed: {e.__class__.__name__} - {e}")
            await _note_rich_result(False, message)
            return index, last_id
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
            rich_html_plain_text(payload.html or payload.markdown or "")
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
            result = await message.reply_text(chunk)
            if getattr(message, "business_connection_id", None):
                bind_business_message(message, result)
        except Exception as e:
            logger.error(
                f"Failed to send rich fallback text: {e.__class__.__name__} - {e}"
            )
            return False
    return True


async def _send_plain_reply(
    message: pyrogram.types.Message,
    markdown: str,
    should_send: Callable[[], bool] | None = None,
) -> pyrogram.types.Message | None:
    """Send markdown as plain text + entities.

    Only splits when the converted text exceeds Telegram's per-message limit;
    a chunk that fails twice is skipped so later chunks still go out. Returns
    the last delivered message, and raises when nothing could be delivered.
    """
    last_msg: pyrogram.types.Message | None = None
    last_error: Exception | None = None
    incomplete_error: Exception | None = None
    for plain, entities in convert_md_chunks(markdown):
        if should_send is not None and not should_send():
            return last_msg
        try:
            last_msg = await message.reply_text(plain, entities=entities)
            if getattr(message, "business_connection_id", None):
                bind_business_message(message, last_msg)
            last_error = None
        except Exception as e:
            logger.warning(f"Send failed: {e.__class__.__name__} - {e}")
            if should_send is not None and not should_send():
                return last_msg
            try:
                last_msg = await message.reply_text(plain)
                if getattr(message, "business_connection_id", None):
                    bind_business_message(message, last_msg)
                last_error = None
            except Exception as e:
                logger.error(f"Send failed: {e.__class__.__name__} - {e}")
                last_error = e
                incomplete_error = e
    if incomplete_error is not None and getattr(
        message, "business_connection_id", None
    ):
        raise incomplete_error
    if last_msg is None and last_error is not None:
        raise last_error
    return last_msg


async def reply_output(
    client: PyrogramClient,
    message: pyrogram.types.Message,
    text: str,
    should_send: Callable[[], bool] | None = None,
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
                    return False
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
                timestamp=datetime.now().timestamp(),
            )
            _chat = message.chat
            _chat_id = _chat.id if _chat else None
            if _chat_id:
                await memttlcache.set(
                    state.bot_last_reply_key(_chat_id),
                    bot_reply,
                    ttl=300,
                )
        return bool(last_reply_id or last_reply_msg or sent_count)
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
    STREAM_EDIT_INTERVAL = 1.5
    MAX_MESSAGE_LENGTH = 4000
    MAX_EDIT_COUNT = 20
    MAX_TOTAL_TIME = float(app_config.agent_streaming_max_time)

    def __init__(
        self,
        client: PyrogramClient,
        message: pyrogram.types.Message,
        should_send: Callable[[], bool] | None = None,
    ):
        self.client = client
        self.message = message
        self.should_send = should_send
        self.current_text = ""
        self._last_sent_text = ""
        self.reply_message_id: int | None = None
        self.reply_message: pyrogram.types.Message | None = None
        self.delivered = False
        self._rich = False
        self.last_edit_time = 0.0
        self.edit_count = 0
        self.start_time = 0.0
        self.is_group_chat = (
            message.chat is not None and message.chat.type in GROUP_CHAT_TYPES
        )
        self.user = message.sender_chat or message.from_user
        self._edit_task: asyncio.Task | None = None
        self._start_task: asyncio.Task | None = None
        self._stop = False
        self.official_draft = (
            OfficialRichDraftStreamer(client, message, should_send=should_send)
            if getattr(message, "business_connection_id", None)
            else None
        )
        self._draft_attempted = False

    def _is_within_limits(self) -> bool:
        current_time = asyncio.get_event_loop().time()
        if self.start_time == 0.0:
            self.start_time = current_time
        elapsed = current_time - self.start_time
        if elapsed > self.MAX_TOTAL_TIME:
            logger.warning(f"Streaming output exceeded max time {self.MAX_TOTAL_TIME}s")
            return False
        if self.edit_count >= self.MAX_EDIT_COUNT:
            logger.warning(
                f"Streaming output exceeded max edit count {self.MAX_EDIT_COUNT}"
            )
            return False
        return True

    async def _edit_message(self, chat_id, message_id, text=None, **kwargs):
        if getattr(self.message, "business_connection_id", None):
            kwargs.pop("business_connection_id", None)
            return await edit_business_message_text(
                self.client,
                self.message,
                message_id,
                text,
                should_send=self.should_send,
                **kwargs,
            )
        return await self.client.edit_message_text(chat_id, message_id, text, **kwargs)

    async def _do_edit(self, text: str):
        if self.should_send is not None and not self.should_send():
            return
        chat = self.message.chat
        if self.reply_message_id is None or chat is None or chat.id is None:
            return
        chat_id = chat.id
        try:
            if self._rich:
                payloads = convert_rich_md(text)
                if not payloads:
                    return
                result = await self._edit_message(
                    chat_id,
                    self.reply_message_id,
                    rich_message=payloads[0],
                    business_connection_id=getattr(
                        self.message, "business_connection_id", None
                    ),
                )
            else:
                # During streaming, send plain text without entities to avoid
                # rendering partially-formed markdown. Entities applied at finalize.
                result = await self._edit_message(
                    chat_id,
                    self.reply_message_id,
                    text[: self.MAX_MESSAGE_LENGTH],
                    parse_mode=pyrogram.enums.ParseMode.DISABLED,
                    business_connection_id=getattr(
                        self.message, "business_connection_id", None
                    ),
                )
            if getattr(self.message, "business_connection_id", None):
                self.reply_message = bind_business_message(self.message, result)
            self._last_sent_text = text
            self.last_edit_time = asyncio.get_event_loop().time()
            self.edit_count += 1
        except pyrogram.errors.exceptions.bad_request_400.MessageNotModified:
            self._last_sent_text = text
        except pyrogram.errors.exceptions.bad_request_400.MessageTooLong:
            await self._send_new_message(text)
        except Exception as e:
            logger.error(f"Error editing message: {e.__class__.__name__} - {e}")

    async def _send_new_message(self, text: str):
        if self.should_send is not None and not self.should_send():
            return
        self._rich = await _rich_output_enabled(self.message)
        if self._rich:
            # Only the first payload opens the stream; the overflow of a
            # >32768-byte answer goes out once at finalize instead of being
            # sent twice.
            payloads = convert_rich_md(text)[:1]
            sent_count, message_id = await _send_rich_payloads(
                self.client, self.message, payloads, should_send=self.should_send
            )
            if sent_count:
                self.delivered = True
                if message_id is None:
                    # Without the id the stream can neither edit nor finalize.
                    raise RuntimeError("Rich streaming reply message was not returned")
                self.reply_message_id = message_id
                self._last_sent_text = text
                self.last_edit_time = asyncio.get_event_loop().time()
                self.edit_count += 1
                return
            self._rich = False
        plain, entities = convert_md(text)
        if getattr(self.message, "business_connection_id", None):
            chunks = convert_md_chunks(text, self.MAX_MESSAGE_LENGTH)
            if not chunks:
                return
            plain, entities = chunks[0]
        if self.should_send is not None and not self.should_send():
            return
        try:
            reply_message = await self.message.reply_text(
                plain[: self.MAX_MESSAGE_LENGTH],
                entities=entities,
            )
        except Exception as e:
            logger.error(f"Send failed in streaming: {e}")
            raise
        if reply_message is None or reply_message.id is None:
            raise RuntimeError("Streaming reply message was not returned")
        self.reply_message_id = reply_message.id
        self.reply_message = reply_message
        if getattr(self.message, "business_connection_id", None):
            bind_business_message(self.message, reply_message)
        self.delivered = True
        self._last_sent_text = text
        self.last_edit_time = asyncio.get_event_loop().time()
        self.edit_count += 1

    async def _edit_loop(self):
        while not self._stop:
            await asyncio.sleep(self.STREAM_EDIT_INTERVAL)
            if self._stop:
                break
            if not self._is_within_limits():
                break
            text = self.current_text
            if not text.strip() or text == self._last_sent_text:
                continue
            await self._do_edit(text)

    async def _start(self):
        await self._send_new_message(self.current_text)
        if self.should_send is not None and not self.should_send():
            return
        self._edit_task = asyncio.create_task(self._edit_loop())

    async def append_delta(self, delta: str):
        if not delta:
            return
        if self.should_send is not None and not self.should_send():
            return
        self.current_text += delta
        if self.official_draft is not None:
            self.official_draft.update(self.current_text)
            if not self._draft_attempted:
                self._draft_attempted = True
                if await self.official_draft.start():
                    return
            elif self.official_draft.supported is not False:
                return
        if self.start_time == 0.0 and self.current_text.strip():
            self.start_time = asyncio.get_event_loop().time()
            self._stop = False
            self._start_task = asyncio.create_task(self._start())

    async def _finalize_rich(self, text: str) -> bool:
        if self.should_send is not None and not self.should_send():
            return False
        chat = self.message.chat
        if chat is None or chat.id is None or self.reply_message_id is None:
            return False
        chat_id = chat.id
        payloads = convert_rich_md(text)
        if not payloads:
            return False
        complete = True
        try:
            result = await self._edit_message(
                chat_id,
                self.reply_message_id,
                rich_message=payloads[0],
                business_connection_id=getattr(
                    self.message, "business_connection_id", None
                ),
            )
            if getattr(self.message, "business_connection_id", None):
                self.reply_message = bind_business_message(self.message, result)
            self._last_sent_text = text
        except pyrogram.errors.exceptions.bad_request_400.MessageNotModified:
            pass
        except Exception as e:
            logger.error(f"Error editing final message: {e.__class__.__name__} - {e}")
            complete = False
        # Long answers are split at Telegram's rich message limits; the
        # overflow goes out even when the final edit failed, so no content is
        # dropped.
        if len(payloads) > 1:
            sent_count, _ = await _send_rich_payloads(
                self.client, self.message, payloads[1:], should_send=self.should_send
            )
            if sent_count < len(payloads) - 1:
                complete = (
                    await _send_rich_tail_plain(
                        self.message,
                        payloads[1 + sent_count :],
                        should_send=self.should_send,
                    )
                    and complete
                )
        return complete

    async def finalize(self):
        self._stop = True
        if self.should_send is not None and not self.should_send():
            await self.abort()
            return False
        if self.official_draft is not None:
            await self.official_draft.stop()
            if not self.delivered and not self._start_task and self.current_text:
                self.delivered = await reply_output(
                    self.client,
                    self.message,
                    self.current_text,
                    should_send=self.should_send,
                )
                return self.delivered
        if self._start_task and not self._start_task.done():
            await self._start_task
        if self._edit_task and not self._edit_task.done():
            self._edit_task.cancel()
            try:
                await self._edit_task
            except asyncio.CancelledError:
                pass
        chat = self.message.chat
        complete = self.delivered
        if (
            self.reply_message_id is not None
            and chat is not None
            and chat.id is not None
            and self.current_text
        ):
            chat_id = chat.id
            text = self.current_text
            if self._rich:
                complete = await self._finalize_rich(text)
            else:
                plain, entities = convert_md(text)
                business_chunks = []
                if getattr(self.message, "business_connection_id", None):
                    business_chunks = convert_md_chunks(text, self.MAX_MESSAGE_LENGTH)
                    if business_chunks:
                        plain, entities = business_chunks[0]
                if text != self._last_sent_text or entities:
                    try:
                        if self.should_send is not None and not self.should_send():
                            return False
                        result = await self._edit_message(
                            chat_id,
                            self.reply_message_id,
                            plain[: self.MAX_MESSAGE_LENGTH],
                            entities=entities,
                            business_connection_id=getattr(
                                self.message, "business_connection_id", None
                            ),
                        )
                        if getattr(self.message, "business_connection_id", None):
                            self.reply_message = bind_business_message(
                                self.message, result
                            )
                        self._last_sent_text = text
                    except (
                        pyrogram.errors.exceptions.bad_request_400.MessageNotModified
                    ):
                        pass
                    except Exception as e:
                        logger.error(f"Error editing final message: {e}")
                        complete = False
                # A fallback preview contains only the first chunk. Deliver
                # the final overflow once, preserving its UTF-16 entities.
                for chunk, chunk_entities in business_chunks[1:]:
                    if self.should_send is not None and not self.should_send():
                        return False
                    try:
                        result = await self.message.reply_text(
                            chunk, entities=chunk_entities
                        )
                    except Exception:
                        if self.should_send is not None and not self.should_send():
                            return False
                        try:
                            result = await self.message.reply_text(chunk)
                        except Exception as e:
                            logger.error(
                                f"Final overflow send failed: {e.__class__.__name__}"
                            )
                            complete = False
                            break
                    bind_business_message(self.message, result)
        if self.reply_message_id and self.is_group_chat and self.user and self.user.id:
            bot_reply = datatype.BotLastReply(
                message_id=self.reply_message_id,
                reply_to_user_id=self.user.id,
                reply_to_message_id=self.message.id,
                reply_text=self.current_text,
                original_user_message=message_plain_text(self.message),
                timestamp=datetime.now().timestamp(),
            )
            chat = self.message.chat
            chat_id = chat.id if chat else None
            if chat_id:
                await memttlcache.set(
                    state.bot_last_reply_key(chat_id),
                    bot_reply,
                    ttl=300,
                )
        return complete

    async def abort(self):
        self._stop = True
        if self.official_draft is not None:
            await self.official_draft.stop()
        for task in (self._start_task, self._edit_task):
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
