import logging
import sys
from datetime import time, timedelta, timezone

from loguru import logger

from waku.config import app_config
from waku.timezone import BOT_TIMEZONE, BOT_TIMEZONE_NAME


class InterceptHandler(logging.Handler):
    def emit(self, record):
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno
        logger.opt(depth=6, exception=record.exc_info).log(level, record.getMessage())


logger.remove()


def _use_bot_timezone(record):
    """Keep log timestamps in UTC+7, including native Windows deployments."""
    converted = record["time"].astimezone(BOT_TIMEZONE)
    # Loguru's Z/ZZ formatter requests utcoffset(None), unsupported by ZoneInfo.
    # Use the offset at this instant and retain the Loguru datetime subclass.
    record["time"] = converted.replace(
        tzinfo=timezone(converted.utcoffset(), BOT_TIMEZONE_NAME)
    )


logger.configure(patcher=_use_bot_timezone)

logger.add(
    "logs/waku.log",
    rotation=time(4, tzinfo=BOT_TIMEZONE),
    enqueue=True,
    encoding="utf-8",
    level="TRACE",
    retention=timedelta(days=app_config.log_retention_days),
)

logger.add(sys.stdout, level=app_config.log_level, enqueue=True)

logging.basicConfig(
    handlers=[InterceptHandler()],
    level=logging.NOTSET,
    force=True,
)

__all__ = ["logger"]
