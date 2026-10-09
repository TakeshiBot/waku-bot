"""Business-aware rich draft previews using the installed Kurigram raw API."""

import asyncio
import secrets
from collections.abc import Callable

import pyrogram
from pyrogram import raw, utils
from pyrogram.client import Client

from waku.logger import logger

# Draft support belongs to an account connection and peer, not the bot chat alone.
_OFFICIAL_DRAFT_UNSUPPORTED_PEERS: set[tuple[str | None, int]] = set()


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
    UPDATE_INTERVAL = 1.0
    MAX_DRAFT_LENGTH = 8192

    def __init__(
        self,
        client: Client,
        message: pyrogram.types.Message,
        should_send: Callable[[], bool] | None = None,
    ):
        self.client = client
        self.message = message
        self.current_text = ""
        self.random_id = secrets.randbits(63)
        self._task: asyncio.Task | None = None
        self._stop = False
        self.supported: bool | None = None
        self.should_send = should_send

    async def _send_draft(self) -> bool:
        chat = self.message.chat
        if chat is None or not self.current_text.strip():
            return False
        if self.should_send is not None and not self.should_send():
            return False
        connection_id = getattr(self.message, "business_connection_id", None)
        peer_key = (connection_id, chat.id)
        if peer_key in _OFFICIAL_DRAFT_UNSUPPORTED_PEERS:
            self.supported = False
            return False
        try:
            peer = await self.client.resolve_peer(chat.id)
            if self.should_send is not None and not self.should_send():
                return False
            await self.client.invoke(
                raw.functions.messages.SetTyping(
                    peer=peer,
                    action=raw.types.InputSendMessageRichMessageDraftAction(
                        random_id=self.random_id,
                        rich_message=raw.types.InputRichMessageMarkdown(
                            markdown=self.current_text[: self.MAX_DRAFT_LENGTH]
                        ),
                    ),
                    top_msg_id=self.message.message_thread_id,
                ),
                business_connection_id=connection_id,
            )
            self.supported = True
            return True
        except Exception as error:
            self.supported = False
            if type(error).__name__ == "TextdraftPeerInvalid":
                _OFFICIAL_DRAFT_UNSUPPORTED_PEERS.add(peer_key)
            logger.debug(f"Rich draft unavailable: {type(error).__name__}")
            return False

    async def _loop(self):
        while not self._stop:
            await asyncio.sleep(self.UPDATE_INTERVAL)
            if self._stop or not await self._send_draft():
                break

    async def start(self) -> bool:
        if self._task is not None:
            return self.supported is not False
        if not await self._send_draft():
            return False
        self._task = asyncio.create_task(self._loop(), name="business-rich-draft")
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
