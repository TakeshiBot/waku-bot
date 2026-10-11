"""Normalize optional vectors before Kurigram parses Business updates."""

from pyrogram import raw

from waku.logger import logger


def install_business_parser(client):
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
