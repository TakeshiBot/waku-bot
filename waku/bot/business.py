"""Normalize optional vectors before Kurigram parses Business updates."""

from pyrogram import enums, raw, types
from pyrogram.handlers import DeletedBusinessMessagesHandler

from waku.logger import logger


def install_business_parser(client):
    kind = raw.types.UpdateBotDeleteBusinessMessage
    parser = client.dispatcher.update_parsers[kind]
    if not getattr(parser, "_waku_business_parser", False):

        async def parse_deleted(update, users, chats, _parser=parser):
            if (
                isinstance(update.peer, raw.types.PeerUser)
                and update.peer.user_id not in users
            ):
                chat = types.Chat(id=update.peer.user_id, type=enums.ChatType.PRIVATE)
                return types.List(
                    [
                        types.Message(
                            id=message_id,
                            chat=chat,
                            business_connection_id=update.connection_id,
                            client=client,
                        )
                        for message_id in update.messages
                    ]
                ), DeletedBusinessMessagesHandler
            return await _parser(update, users, chats)

        parse_deleted._waku_business_parser = True
        client.dispatcher.update_parsers[kind] = parse_deleted
    for kind in (
        raw.types.UpdateBotNewBusinessMessage,
        raw.types.UpdateBotEditBusinessMessage,
    ):
        parser = client.dispatcher.update_parsers[kind]
        if getattr(parser, "_waku_business_parser", False):
            continue

        async def parse(update, users, chats, _parser=parser):
            message = update.message
            logger.debug(
                "Telegram Business raw update (kind={}, incoming={}, direct_bot={}, via_business={}, rich={})",
                type(update).__name__,
                not bool(getattr(message, "out", False)),
                bool(
                    getattr(
                        users.get(
                            getattr(getattr(message, "from_id", None), "user_id", None)
                        ),
                        "bot",
                        False,
                    )
                ),
                bool(getattr(message, "via_business_bot_id", None)),
                bool(getattr(message, "rich_message", None)),
            )
            # Kurigram iterates this nullable field without an `or []` guard.
            for message in (update.message, getattr(update, "reply_to_message", None)):
                if (
                    message is not None
                    and hasattr(message, "restriction_reason")
                    and message.restriction_reason is None
                ):
                    message.restriction_reason = []
            return await _parser(update, users, chats)

        setattr(parse, "_waku_business_parser", True)
        client.dispatcher.update_parsers[kind] = parse
