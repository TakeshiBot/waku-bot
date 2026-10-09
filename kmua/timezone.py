"""Explicit bot wall-clock time, independent of the host operating system."""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

BOT_TIMEZONE_NAME = "Asia/Ho_Chi_Minh"
BOT_TIMEZONE = ZoneInfo(BOT_TIMEZONE_NAME)


def bot_now() -> datetime:
    """Return an aware current timestamp in the bot's UTC+7 timezone."""
    return datetime.now(BOT_TIMEZONE)


def as_bot_time(value: datetime, *, naive_timezone=UTC) -> datetime:
    """Display a timestamp in UTC+7; naive database timestamps default to UTC.

    Pass ``naive_timezone=None`` for Telegram dates returned as host-local naive
    datetimes by Pyrogram. Already aware timestamps preserve their instant.
    """
    if value.tzinfo is None and naive_timezone is not None:
        value = value.replace(tzinfo=naive_timezone)
    return value.astimezone(BOT_TIMEZONE)
