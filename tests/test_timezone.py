"""Bot UTC+7 behavior without starting Telegram, model services, or a database."""

import ast
import datetime as datetime_module
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger
from loguru import logger
from lxml import html as lxml_html
from sqlalchemy import create_engine

from kmua.plugins.agent.localization import locale_scope, tr
from kmua.timezone import BOT_TIMEZONE, BOT_TIMEZONE_NAME, as_bot_time, bot_now

ROOT = Path(__file__).resolve().parents[1]


def load_definitions(filename, names, **namespace):
    """Run source definitions while excluding imports with bot startup effects."""
    tree = ast.parse((ROOT / filename).read_text(encoding="utf-8"))
    definitions = [node for node in tree.body if getattr(node, "name", None) in names]
    assert {node.name for node in definitions} == names
    future = ast.ImportFrom(
        module="__future__", names=[ast.alias(name="annotations")], level=0
    )
    module = ast.fix_missing_locations(
        ast.Module(body=[future, *definitions], type_ignores=[])
    )
    namespace.update(BOT_TIMEZONE=BOT_TIMEZONE, tr=tr)
    exec(compile(module, filename, "exec"), namespace)
    return SimpleNamespace(**namespace)


def test_bot_clock_is_aware_utc_plus_seven():
    current = bot_now()
    assert BOT_TIMEZONE_NAME == "Asia/Ho_Chi_Minh"
    assert current.tzinfo == BOT_TIMEZONE
    assert current.utcoffset() == timedelta(hours=7)
    assert abs((current - datetime.now(UTC)).total_seconds()) < 5


def test_storage_and_telegram_timestamps_preserve_their_instant():
    utc = datetime(2026, 10, 9, 20, 30, tzinfo=UTC)
    shown = as_bot_time(utc)
    assert shown.isoformat() == "2026-10-10T03:30:00+07:00"
    assert shown.timestamp() == utc.timestamp()
    assert as_bot_time(utc.replace(tzinfo=None)) == shown
    # Pyrogram returns a naive host-local datetime from Unix timestamps.
    telegram_date = datetime.fromtimestamp(utc.timestamp())
    assert as_bot_time(telegram_date, naive_timezone=None) == shown


def test_naive_schedules_use_bot_time_and_explicit_offsets_are_honored():
    functions = load_definitions(
        "kmua/plugins/agent/tools/send_ops.py",
        {"_parse_schedule_time"},
        datetime=datetime_module,
    )
    naive = functions._parse_schedule_time("2026-10-10T09:15:00")
    assert naive.isoformat() == "2026-10-10T09:15:00+07:00"
    assert naive.astimezone(UTC).hour == 2
    for value in ("2026-10-10T02:15:00Z", "2026-10-10T04:15:00+02:00"):
        explicit = functions._parse_schedule_time(value)
        assert explicit == naive
        assert explicit.utcoffset() == datetime.fromisoformat(value).utcoffset()
    with pytest.raises(ValueError):
        functions._parse_schedule_time("invalid")


@pytest.mark.asyncio
async def test_current_time_and_naive_time_difference_use_bot_timezone():
    instant = datetime(2026, 10, 9, 20, 30, tzinfo=UTC)

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return instant.astimezone(tz) if tz else instant.replace(tzinfo=None)

    functions = load_definitions(
        "kmua/plugins/agent/tools/time.py",
        {"_NowResult", "_DifferenceResult", "_now", "_difference"},
        dataclass=dataclass,
        datetime=FrozenDatetime,
        UTC=UTC,
    )
    with locale_scope("vi"):
        local = await functions._now("local", "both")
        assert local.success
        assert local.iso_format == "2026-10-10T03:30:00+07:00"
        assert local.timezone == BOT_TIMEZONE_NAME
        utc = await functions._now("UTC", "iso")
        assert utc.success and utc.iso_format == instant.isoformat()
        berlin = await functions._now("Europe/Berlin", "iso")
        assert berlin.success
        assert (
            datetime.fromisoformat(berlin.iso_format).timestamp() == instant.timestamp()
        )
        invalid = await functions._now("Not/A_Timezone", "both")
        assert not invalid.success
        difference = await functions._difference(
            "2026-10-10 09:15", "2026-10-10T02:15:00Z"
        )
        assert difference.success and "0 giây" in difference.message


@pytest.fixture
def scheduler():
    engine = create_engine("sqlite:///:memory:")
    definitions = load_definitions(
        "kmua/common/jobs.py",
        {"_TaskScheduler"},
        datetime=datetime_module,
        MemoryJobStore=MemoryJobStore,
        SQLAlchemyJobStore=SQLAlchemyJobStore,
        AsyncIOScheduler=AsyncIOScheduler,
        DateTrigger=DateTrigger,
        IntervalTrigger=IntervalTrigger,
        CronTrigger=CronTrigger,
        sync_engine=engine,
        logger=logger,
    )
    queue = definitions._TaskScheduler()
    queue._add_job_with_fallback = lambda **kwargs: kwargs["trigger"]
    yield queue
    engine.dispose()


def test_scheduler_naive_boundaries_and_daily_wall_clock_are_utc_plus_seven(scheduler):
    assert scheduler._scheduler.timezone == BOT_TIMEZONE
    naive = datetime(2026, 10, 10, 9, 15)
    once = scheduler.add_onetime_job("test", lambda: None, naive)
    assert once.run_date.isoformat() == "2026-10-10T09:15:00+07:00"
    aware = naive.replace(tzinfo=UTC)
    explicit = scheduler.add_onetime_job("test", lambda: None, aware)
    assert explicit.run_date == aware and explicit.run_date.utcoffset() == timedelta(0)
    interval = scheduler.add_interval_job(
        "test", lambda: None, hours=1, start_date="2026-10-10 09:15:00"
    )
    assert interval.timezone == BOT_TIMEZONE
    assert interval.start_date.isoformat() == "2026-10-10T09:15:00+07:00"
    daily = scheduler.add_daily_job("cleanup", lambda: None, hour=4)
    next_run = daily.get_next_fire_time(None, datetime(2026, 10, 9, 20, tzinfo=UTC))
    assert next_run.isoformat() == "2026-10-10T04:00:00+07:00"
    assert next_run.astimezone(UTC).isoformat() == "2026-10-09T21:00:00+00:00"
    explicit_daily = scheduler.add_daily_job("test", lambda: None, hour=4, timezone=UTC)
    assert explicit_daily.timezone == UTC


def test_prompt_clock_includes_the_bot_offset():
    definitions = load_definitions(
        "kmua/plugins/agent/input_format.py",
        {"_now_text"},
        bot_now=lambda: datetime(2026, 10, 10, 3, 30, tzinfo=BOT_TIMEZONE),
    )
    assert definitions._now_text() == "2026-10-10 03:30:00 +0700"


@pytest.mark.parametrize(
    "page",
    [
        '<html><span id="publish_time">2026-10-10 00:30</span></html>',
        '<html><script>var ct = "1791563400"</script></html>',
    ],
)
def test_wechat_publication_times_convert_the_source_instant_to_bot_time(page):
    functions = load_definitions(
        "kmua/services/wechat.py",
        {"WechatBlock", "WechatArticle", "parse_article_html"},
        datetime=datetime,
        timezone=datetime_module.timezone,
        timedelta=timedelta,
        dataclass=dataclass,
        field=field,
        re=re,
        lxml_html=lxml_html,
        _meta_content=lambda *args: "",
        _paragraphs_from_html=lambda *args: [],
        _images_from_html=lambda *args: [],
        _blocks_from_html=lambda *args: [],
    )
    article = functions.parse_article_html(page, "https://mp.weixin.qq.com/s/test")
    assert article.published_at.isoformat() == "2026-10-09T23:30:00+07:00"


def test_log_records_and_daily_rotation_use_bot_timezone(tmp_path):
    # Run the real logger module separately: its handler setup is global and
    # writes files, so neither effect should leak into the test runner.
    script = f"""
import sys
from datetime import timedelta
from types import SimpleNamespace
sys.path.insert(0, {str(ROOT)!r})
sys.modules['kmua.config'] = SimpleNamespace(app_config=SimpleNamespace(log_retention_days=1, log_level='ERROR'))
from kmua.logger import logger
from kmua.timezone import BOT_TIMEZONE, BOT_TIMEZONE_NAME
captured = []
handler = logger.add(lambda message: captured.append(message), format='{{time:YYYY-MM-DD HH:mm:ss.SSS ZZ}}')
logger.info('timezone check')
record = captured[0].record
assert str(record['time'].tzinfo) == BOT_TIMEZONE_NAME
assert record['time'].utcoffset() == timedelta(hours=7)
assert '+0700' in str(captured[0])
file_sink = next(item._sink for item in logger._core.handlers.values() if hasattr(item._sink, '_rotation_function'))
rotation_time = file_sink._rotation_function._time_init
assert rotation_time.hour == 4 and rotation_time.tzinfo == BOT_TIMEZONE
assert file_sink._path == 'logs/waku.log'
logger.complete()
logger.remove()
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
