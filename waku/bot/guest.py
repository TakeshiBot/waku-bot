"""Parse guest updates in an isolated cache, including ephemeral-message query IDs."""

from pyrogram import raw, types
from pyrogram.handlers import GuestMessageHandler

from waku.logger import logger


class _GuestCache:
    def __init__(self):
        self.messages = {}

    async def get(self, key):
        return self.messages.get(key)

    async def set(self, key, value):
        self.messages[key] = value


class _GuestParserClient:
    def __init__(self, client):
        self.client = client
        self.message_cache = _GuestCache()
        self.fetch_replies = False

    def __getattr__(self, name):
        return getattr(self.client, name)

    async def get_messages(self, chat_id, message_ids=None, **kwargs):
        # Guest replies may use IDs from an independent chat namespace. Never
        # look them up in an ordinary chat or borrow its cached messages.
        if isinstance(message_ids, int):
            return await self.message_cache.get((chat_id, message_ids))
        return [
            await self.message_cache.get((chat_id, mid)) for mid in message_ids or []
        ]


def install_guest_parser(client):
    async def parse_guest(update, users, chats):
        logger.info(
            "Telegram Guest query received (references={})",
            len(update.reference_messages or []),
        )
        scoped = _GuestParserClient(client)

        async def parse(message, query_id=None):
            # The SDK iterates this optional vector without checking for None.
            if (
                hasattr(message, "restriction_reason")
                and message.restriction_reason is None
            ):
                message.restriction_reason = []
            return await types.Message._parse(
                scoped, message, users, chats, replies=0, guest_query_id=query_id
            )

        references = [await parse(ref) for ref in update.reference_messages or []]
        message = await parse(update.message, str(update.query_id))
        # Kurigram's ephemeral branch does not forward this argument yet.
        message.guest_query_id = str(update.query_id)
        message.guest_reference_messages = references
        return message, GuestMessageHandler

    client.dispatcher.update_parsers[raw.types.UpdateBotGuestChatQuery] = parse_guest
