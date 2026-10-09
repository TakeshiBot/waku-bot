"""Read the current time or calculate the difference between timestamps."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from pydantic_ai import RunContext

from waku.plugins.agent.localization import tr
from waku.timezone import BOT_TIMEZONE

from .. import datatype

# Any IANA timezone name is accepted; "local" and "UTC" are special-cased.
TimezoneName = str


@dataclass
class _NowResult:
    success: bool = True
    message: str = ""
    iso_format: str = ""
    readable_format: str = ""
    timezone: str = ""


@dataclass
class _DifferenceResult:
    success: bool = True
    message: str = ""


async def _now(
    timezone_name: TimezoneName, format_type: Literal["iso", "readable", "both"]
) -> _NowResult:
    from zoneinfo import ZoneInfo

    try:
        utc_now = datetime.now(UTC)
        if timezone_name == "UTC":
            target_time = utc_now
            tz_info = UTC
        elif timezone_name == "local":
            tz_info = BOT_TIMEZONE
            target_time = utc_now.astimezone(tz_info)
        else:
            tz_info = ZoneInfo(timezone_name)
            target_time = utc_now.astimezone(tz_info)

        iso_str = target_time.isoformat()
        weekdays = [
            tr("monday"),
            tr("tuesday"),
            tr("wednesday"),
            tr("thursday"),
            tr("friday"),
            tr("saturday"),
            tr("sunday"),
        ]
        weekday = weekdays[target_time.weekday()]
        readable_str = target_time.strftime(tr("readable_date_format", p0=weekday))

        if format_type == "iso":
            message = tr("current_time_iso", p0=iso_str)
        elif format_type == "readable":
            message = tr("current_time", p0=readable_str)
        else:
            message = tr("current_time_both", p0=iso_str, p1=readable_str)

        return _NowResult(
            success=True,
            message=message,
            iso_format=iso_str,
            readable_format=readable_str,
            timezone=str(tz_info) if tz_info else "Unknown",
        )
    except Exception as e:
        return _NowResult(success=False, message=tr("time_failed", p0=e))


async def _difference(time1: str, time2: str) -> _DifferenceResult:
    from dateutil import parser

    try:
        dt1 = parser.parse(time1)
        dt2 = parser.parse(time2)
        if dt1.tzinfo is None:
            dt1 = dt1.replace(tzinfo=BOT_TIMEZONE)
        if dt2.tzinfo is None:
            dt2 = dt2.replace(tzinfo=BOT_TIMEZONE)
        diff = dt2.astimezone(UTC) - dt1.astimezone(UTC)
        total_seconds = abs(diff.total_seconds())
        days = int(total_seconds // 86400)
        hours = int((total_seconds % 86400) // 3600)
        minutes = int((total_seconds % 3600) // 60)
        seconds = int(total_seconds % 60)
        parts = []
        if days > 0:
            parts.append(tr("days", p0=days))
        if hours > 0:
            parts.append(tr("hours", p0=hours))
        if minutes > 0:
            parts.append(tr("minutes", p0=minutes))
        if seconds > 0 or not parts:
            parts.append(tr("seconds", p0=seconds))
        time_diff_str = " ".join(parts)
        direction = tr("time_after") if diff.total_seconds() >= 0 else tr("time_before")
        message = tr(
            "time_difference",
            p0=time_diff_str,
            p1=time1,
            p2=time2,
            p3=direction,
            p4=format(abs(diff.total_seconds()), ".0f"),
        )
        return _DifferenceResult(success=True, message=message)
    except Exception as e:
        return _DifferenceResult(
            success=False, message=tr("time_difference_failed", p0=e)
        )


async def time_info(
    ctx: RunContext[datatype.ContextDeps],
    operation: Literal["now", "difference"] = "now",
    time1: str | None = None,
    time2: str | None = None,
    timezone_name: TimezoneName = "local",
    format_type: Literal["iso", "readable", "both"] = "both",
) -> str:
    """Get the current date and time, or the gap between two timestamps.

    Args:
        operation: "now" reads the current time; "difference" compares two times.
        time1, time2: Required for "difference" — timestamps in ISO format
            (e.g. "2026-08-02T10:00:00+07:00") or date strings such as
            "2026-08-02 10:00"; naive timestamps use the bot's UTC+7 timezone.
        timezone_name: The timezone "now" is reported in — "local" (default)
            is "Asia/Ho_Chi_Minh" (UTC+7), or any IANA name like "Asia/Ho_Chi_Minh",
            "Europe/Berlin". Ignored for "difference".
        format_type: How "now" is formatted: "iso", "readable", or "both" (default).
    """
    if operation == "now":
        result = await _now(timezone_name, format_type)
        if not result.success:
            return tr("error", p0=result.message)
        return result.message
    if time1 is None or time2 is None:
        return tr("time_difference_required")
    result = await _difference(time1, time2)
    if not result.success:
        return tr("error", p0=result.message)
    return result.message


__all__ = ["time_info"]
