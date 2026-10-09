from enum import IntEnum, StrEnum


# https://python-telegram-bot.org/
class ChatID(IntEnum):
    """This enum contains some special chat IDs. The enum
    members of this enumeration are instances of :class:`int` and can be treated as such.

    """

    __slots__ = ()

    ANONYMOUS_ADMIN = 1087968824
    """:obj:`int`: User ID in groups for messages sent by anonymous admins. Telegram chat:
    `@GroupAnonymousBot <https://t.me/GroupAnonymousBot>`_.

    Note:
        :attr:`telegram.Message.from_user` will contain this ID for backwards compatibility only.
        It's recommended to use :attr:`telegram.Message.sender_chat` instead.
    """
    SERVICE_CHAT = 777000
    """:obj:`int`: Telegram service chat, that also acts as sender of channel posts forwarded to
    discussion groups. Telegram chat: `Telegram <https://t.me/+42777>`_.

    Note:
        :attr:`telegram.Message.from_user` will contain this ID for backwards compatibility only.
        It's recommended to use :attr:`telegram.Message.sender_chat` instead.
    """
    FAKE_CHANNEL = 136817688
    """:obj:`int`: User ID in groups when message is sent on behalf of a channel, or when a channel
    votes on a poll. Telegram chat: `@Channel_Bot <https://t.me/Channel_Bot>`_.
    """


class GLockKey(StrEnum):
    """This enum contains keys for the global lock."""

    __slots__ = ()

    CLEANING = "cleaning"


class VerifyTrigger(StrEnum):
    """When to trigger member verification, independent of the verification method."""

    __slots__ = ()

    ALL = "all"
    FIRST_MESSAGE = "first_message"


class VerifyMethod(StrEnum):
    """How to verify new members, independent of the trigger policy."""

    __slots__ = ()

    MATH_EASY = "math_easy"
    MATH_HARD = "math_hard"
    EMOJI = "emoji"
    STICKER = "sticker"
    CUSTOM_QA = "custom_qa"


class VerifyFailAction(StrEnum):
    """Action taken after verification fails by timeout or exhausted attempts."""

    __slots__ = ()

    KICK = "kick"  # ban + unban: remove without blacklisting; rejoining allows another verification.
    BAN = "ban"  # Permanently ban.
    UNRESTRICT = "unrestrict"  # Remove restrictions and keep the member in the group.
