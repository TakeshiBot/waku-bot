"""Native Telegram generation drafts and Business-aware output adapters."""

import asyncio
import secrets
from collections import OrderedDict
from collections.abc import Callable

import pyrogram
from pyrogram import raw, utils
from pyrogram.client import Client

from waku.common.rich_message import sent_message_id
from waku.logger import logger
from waku.plugins.agent.styling import convert_md_chunks, convert_rich_md

# Draft support belongs to an account connection and peer, not the bot chat alone.
_OFFICIAL_DRAFT_UNSUPPORTED_PEERS: set[tuple[int, str | None, int]] = set()
# Multiple forum topics share a peer's typing quota. Reserve at most one
# preview per second across those streams, with bounded account/peer state.
_DRAFT_PEER_NEXT_SEND: OrderedDict[tuple[int, str | None, int], float] = OrderedDict()


def bind_business_message(source, result):
    """Native sends may return a Message without its Business connection field."""
    connection_id = getattr(source, "business_connection_id", None)
    if connection_id and result is not None:
        result.business_connection_id = connection_id
    return result


async def edit_business_message_text(
    client: Client,
    source: pyrogram.types.Message,
    message_id: int,
    text: str | None = None,
    *,
    entities: list[pyrogram.types.MessageEntity] | None = None,
    parse_mode: pyrogram.enums.ParseMode | None = None,
    rich_message: pyrogram.types.InputRichMessage | None = None,
    should_send: Callable[[], bool] | None = None,
) -> pyrogram.types.Message | None:
    """Keep explicit entities and Business edit updates the SDK drops."""
    if source.chat is None:
        return None
    peer = await client.resolve_peer(source.chat.id)
    parsed = (
        await utils.parse_text_entities(client, text, parse_mode, entities)
        if text is not None
        else {"message": "", "entities": None}
    )
    if should_send is not None and not should_send():
        return None
    connection_id = source.business_connection_id
    result = await client.invoke(
        raw.functions.messages.EditMessage(
            peer=peer,
            id=message_id,
            message=parsed["message"],
            entities=parsed["entities"],
            rich_message=rich_message.write() if rich_message is not None else None,
        ),
        business_connection_id=connection_id,
    )
    updates = getattr(result, "updates", None)
    if updates is None:
        updates = [getattr(result, "update", result)]
    for update in updates:
        if isinstance(
            update,
            (
                raw.types.UpdateBotEditBusinessMessage,
                raw.types.UpdateEditMessage,
                raw.types.UpdateEditChannelMessage,
            ),
        ):
            # Short updates carry no peer dictionaries. Build a bound native
            # Message from the known chat instead of fetching missing peers
            # after a successful edit (or letting the SDK parser fail).
            if not getattr(result, "users", None) and not getattr(
                result, "chats", None
            ):
                return pyrogram.types.Message(
                    id=update.message.id,
                    chat=source.chat,
                    text=parsed["message"] or None,
                    entities=entities,
                    business_connection_id=connection_id,
                    outgoing=True,
                    raw=update.message,
                    client=client,
                )
            return await pyrogram.types.Message._parse(
                client,
                update.message,
                {user.id: user for user in getattr(result, "users", [])},
                {chat.id: chat for chat in getattr(result, "chats", [])},
                business_connection_id=connection_id,
                replies=0,
            )
    return None


class OfficialRichDraftStreamer:
    """Native private-chat drafts, never edits of already-delivered messages."""

    UPDATE_INTERVAL = 1.0
    REFRESH_INTERVAL = 10.0
    MAX_DRAFT_LENGTH = 4000
    MAX_TRANSIENT_FAILURES = 3

    def __init__(
        self, client, message, should_send=None, *, rich=False, max_duration=None
    ):
        self.client = client
        self.message = message
        self.current_text = ""
        self.random_id = secrets.randbits(63) or 1
        self.rich = rich
        self._task = None
        self._stop = False
        self.supported = None
        self.should_send = should_send
        self._last_text = None
        self._last_send = 0.0
        self._next_send = 0.0
        self._started_at = 0.0
        self.max_duration = max_duration
        self._transient_failures = 0
        self._empty_rich_text = None

    async def _send_draft(self, *, force_text=False, thinking=False) -> bool:
        chat = self.message.chat
        if chat is None or self._stop:
            return False
        if self.should_send is not None and not self.should_send():
            return False
        connection_id = getattr(self.message, "business_connection_id", None)
        peer_key = (id(self.client), connection_id, chat.id)
        if peer_key in _OFFICIAL_DRAFT_UNSUPPORTED_PEERS:
            self.supported = False
            return False
        now = asyncio.get_running_loop().time()
        if now < self._next_send:
            return True
        if (
            self.current_text == self._last_text
            and now - self._last_send < self.REFRESH_INTERVAL
        ):
            return True
        draft_text = self.current_text
        try:
            peer = await self.client.resolve_peer(chat.id)
            if not isinstance(peer, raw.types.InputPeerUser):
                self.supported = False
                return False
            if self.should_send is not None and not self.should_send():
                return False
            chunks = convert_md_chunks(draft_text, self.MAX_DRAFT_LENGTH)
            text, entities = chunks[0] if chunks else ("", [])
            # Publish a real Thinking block before the model emits text.
            # Empty text drafts remain the native fallback for rich rejection.
            thinking = thinking or draft_text == self._empty_rich_text
            if self.rich and not force_text and not draft_text.strip():
                action = raw.types.InputSendMessageRichMessageDraftAction(
                    random_id=self.random_id,
                    rich_message=raw.types.InputRichMessage(
                        blocks=[
                            raw.types.PageBlockThinking(
                                text=raw.types.TextPlain(text="Thinking…")
                            )
                        ],
                    ),
                    can_stop=True,
                )
            elif self.rich and not force_text and not thinking and text.strip():
                payloads = convert_rich_md(draft_text)
                if payloads:
                    action = raw.types.InputSendMessageRichMessageDraftAction(
                        random_id=self.random_id,
                        rich_message=payloads[0].write(),
                        can_stop=True,
                    )
                else:
                    action = None
            else:
                action = None
            if action is None:
                if thinking:
                    text, entities = "", []
                elif force_text:
                    entities = []
                parsed = await utils.parse_text_entities(
                    self.client,
                    text,
                    pyrogram.enums.ParseMode.DISABLED,
                    entities,
                )
                action = raw.types.SendMessageTextDraftAction(
                    random_id=self.random_id,
                    text=raw.types.TextWithEntities(
                        text=parsed["message"],
                        entities=parsed["entities"] or [],
                    ),
                    can_stop=True,
                )
            if self._stop or (self.should_send is not None and not self.should_send()):
                return False
            now = asyncio.get_running_loop().time()
            if now < _DRAFT_PEER_NEXT_SEND.get(peer_key, 0):
                return True
            _DRAFT_PEER_NEXT_SEND[peer_key] = now + self.UPDATE_INTERVAL
            _DRAFT_PEER_NEXT_SEND.move_to_end(peer_key)
            if len(_DRAFT_PEER_NEXT_SEND) > 4096:
                _DRAFT_PEER_NEXT_SEND.popitem(last=False)
            result = await self.client.invoke(
                raw.functions.messages.SetTyping(
                    peer=peer,
                    action=action,
                    top_msg_id=getattr(self.message, "message_thread_id", None),
                ),
                business_connection_id=connection_id,
            )
            if not result:
                self.supported = False
                return False
            self.supported = True
            self._transient_failures = 0
            self._last_text = draft_text
            self._last_send = now
            self._next_send = now + self.UPDATE_INTERVAL
            return True
        except pyrogram.errors.FloodWait as error:
            # FloodWait means supported but temporarily throttled, not an
            # unsupported peer. Coalesce new deltas during the cooldown.
            self._next_send = now + max(1, error.value)
            _DRAFT_PEER_NEXT_SEND[peer_key] = self._next_send
            return True
        except pyrogram.errors.RichMessageUnsupported:
            if self.rich and not force_text:
                self.rich = False
                _DRAFT_PEER_NEXT_SEND.pop(peer_key, None)
                return await self._send_draft()
            self.supported = False
            return False
        except Exception as error:
            # Kurigram 2.2.25 has no named RICH_MESSAGE_EMPTY exception;
            # unknown 400 errors carry the exact server code in .value.
            empty_rich = (
                isinstance(error, pyrogram.errors.BadRequest)
                and error.value == "[400 RICH_MESSAGE_EMPTY]"
            )
            format_rejected = isinstance(
                error,
                (
                    pyrogram.errors.EntityBoundsInvalid,
                    pyrogram.errors.EntitiesTooLong,
                    pyrogram.errors.MessageTooLong,
                ),
            )
            if not force_text and (empty_rich or format_rejected):
                if empty_rich:
                    self._empty_rich_text = draft_text
                _DRAFT_PEER_NEXT_SEND.pop(peer_key, None)
                logger.debug("Native draft format rejected; continuing with text draft")
                return await self._send_draft(force_text=True, thinking=empty_rich)
            # Drafts are ephemeral and idempotent by random_id. A transient
            # lost ACK may be retried with that same ID, unlike final sends.
            transient = isinstance(error, (OSError, TimeoutError)) or (
                isinstance(error, pyrogram.errors.RPCError) and error.CODE >= 500
            )
            if transient and self._transient_failures < self.MAX_TRANSIENT_FAILURES:
                self._transient_failures += 1
                self._next_send = now + 2**self._transient_failures
                _DRAFT_PEER_NEXT_SEND[peer_key] = self._next_send
                logger.debug(f"Native draft retry scheduled: {type(error).__name__}")
                return True
            self.supported = False
            if isinstance(error, pyrogram.errors.TextdraftPeerInvalid):
                _OFFICIAL_DRAFT_UNSUPPORTED_PEERS.add(peer_key)
            logger.debug(f"Native draft unavailable: {type(error).__name__}")
            return False

    async def _loop(self):
        while not self._stop:
            await asyncio.sleep(self.UPDATE_INTERVAL)
            if (
                self.max_duration
                and asyncio.get_running_loop().time() - self._started_at
                >= self.max_duration
            ):
                break
            if self._stop or not await self._send_draft():
                break

    async def start(self) -> bool:
        if self._task is not None:
            return self.supported is not False
        if not await self._send_draft():
            return False
        self._started_at = asyncio.get_running_loop().time()
        self._task = asyncio.create_task(self._loop(), name="telegram-native-draft")
        return True

    def update(self, text: str) -> None:
        self.current_text = text

    async def stop(self) -> None:
        self._stop = True
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass


async def send_generation_message(
    client,
    source,
    random_id,
    *,
    text="",
    entities=None,
    rich_message=None,
    should_send=None,
):
    """Persist a draft using the same random ID and SDK reply/topic builder.

    RPC retries reuse this exact request. Unknown send errors never trigger a
    second formatting attempt: the first request may already be delivered.
    """
    if source.chat is None or (should_send is not None and not should_send()):
        return None
    peer = await client.resolve_peer(source.chat.id)
    reply_to = await utils.get_reply_to(
        client,
        pyrogram.types.ReplyParameters(message_id=source.id),
        getattr(source, "message_thread_id", None),
        getattr(source, "direct_messages_topic_id", None),
    )
    parsed = (
        await utils.parse_text_entities(
            client,
            text,
            pyrogram.enums.ParseMode.DISABLED,
            entities,
        )
        if rich_message is None
        else {"message": "", "entities": None}
    )
    query = raw.functions.messages.SendMessage(
        peer=peer,
        random_id=random_id,
        reply_to=reply_to,
        message=parsed["message"],
        entities=parsed["entities"],
        rich_message=rich_message.write() if rich_message is not None else None,
    )
    while should_send is None or should_send():
        try:
            result = await client.invoke(
                query,
                business_connection_id=getattr(source, "business_connection_id", None),
            )
            return sent_message_id(result)
        except pyrogram.errors.FloodWait as error:
            await asyncio.sleep(max(1, error.value))
    return None
