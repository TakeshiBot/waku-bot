"""Discord saved-job compatibility, startup readiness and bounded retry."""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from waku.discordbot import scheduling, state
from waku.timezone import BOT_TIMEZONE


def test_schedule_timezone_uses_shared_vietnam_timezone():
    assert scheduling._discord_schedule_timezone() is BOT_TIMEZONE


def test_new_jobs_keep_old_discord_misfire_grace(monkeypatch):
    job = MagicMock()
    add_job = MagicMock(return_value=job)
    monkeypatch.setattr(scheduling.common.jobqueue, "add_onetime_job", add_job)
    result = scheduling._add_discord_job("add_onetime_job", "id", object(), datetime.now(UTC))
    assert result is job
    job.modify.assert_called_once_with(misfire_grace_time=60, coalesce=True)


def test_grace_configuration_accepts_real_apscheduler_job(monkeypatch):
    scheduler = AsyncIOScheduler(timezone=BOT_TIMEZONE)

    def add_job(job_id, func, run_date, args=None):
        return scheduler.add_job(func, "date", run_date=run_date, id=job_id, args=args or [])

    monkeypatch.setattr(scheduling.common.jobqueue, "add_onetime_job", add_job)
    job = scheduling._add_discord_job(
        "add_onetime_job", "discord-grace-test", scheduling._scheduled_discord_text_job,
        datetime.now(UTC) + timedelta(hours=1), args=[2, [3], "Reminder"],
    )
    assert job.misfire_grace_time == 60
    assert job.coalesce is True
    assert job.func_ref == "waku.discordbot.scheduling:_scheduled_discord_text_job"


@pytest.mark.parametrize(
    ("prefix", "args", "kind", "recurring"),
    [
        ("discord_schedule_msg", [2, [3], "text"], "message", False),
        ("discord_schedule_repeat_msg", [2, [3], "text"], "message", True),
        ("discord_schedule_image", [2, "anime", "topic"], "image", False),
        ("discord_schedule_repeat_image", [2, "web", "topic"], "image", True),
    ],
)
def test_original_persistent_ids_and_args_are_readable(prefix, args, kind, recurring):
    job = SimpleNamespace(
        id=f"{prefix}:1:2:3:12345:hash_memory",
        args=args,
        next_run_time=datetime.now(UTC),
    )
    parsed = scheduling._parse_discord_scheduled_job(job)
    assert parsed.job_id == job.id.removesuffix("_memory")
    assert (parsed.guild_id, parsed.channel_id, parsed.user_id) == (1, 2, 3)
    assert (parsed.kind, parsed.recurring) == (kind, recurring)


@pytest.mark.asyncio
async def test_job_waits_for_discord_ready_during_startup(monkeypatch):
    ready = False
    client = SimpleNamespace(is_ready=lambda: ready)
    monkeypatch.setattr(scheduling.app_config, "discord_enabled", True)
    monkeypatch.setattr(state, "discord_stopping", False)
    monkeypatch.setattr(state, "discord_client", client)
    monkeypatch.setattr(state, "discord_task", None)
    monkeypatch.setattr(scheduling, "_DISCORD_READY_POLL", 0.001)
    waiter = asyncio.create_task(scheduling._ready_discord_client())
    await asyncio.sleep(0.002)
    assert not waiter.done()
    ready = True
    assert await asyncio.wait_for(waiter, timeout=1) is client


@pytest.mark.asyncio
async def test_disabled_and_stopping_jobs_do_not_wait(monkeypatch):
    monkeypatch.setattr(state, "discord_client", None)
    monkeypatch.setattr(scheduling.app_config, "discord_enabled", False)
    assert await scheduling._ready_discord_client() is None
    monkeypatch.setattr(scheduling.app_config, "discord_enabled", True)
    monkeypatch.setattr(state, "discord_stopping", True)
    assert await scheduling._ready_discord_client() is None


@pytest.mark.asyncio
async def test_one_time_job_is_persistently_retried_with_original_id(monkeypatch):
    job_id = "discord_schedule_msg:1:2:3:12345:hash"
    add_job = MagicMock()
    monkeypatch.setattr(scheduling.app_config, "discord_enabled", True)
    monkeypatch.setattr(scheduling, "_ready_discord_client", AsyncMock(return_value=None))
    monkeypatch.setattr(scheduling.common.jobqueue, "get_job", lambda _: None)
    monkeypatch.setattr(scheduling.common.jobqueue, "add_onetime_job", add_job)
    await scheduling._scheduled_discord_text_job(2, [3], "text", False, job_id)
    args, kwargs = add_job.call_args
    assert args[0] == job_id
    assert args[1] is scheduling._scheduled_discord_text_job
    assert datetime.now(UTC) < args[2] < datetime.now(UTC) + timedelta(seconds=61)
    assert kwargs["args"] == [2, [3], "text", False, job_id, None, 1]


def test_recurring_interval_is_never_replaced_and_retries_are_bounded(monkeypatch):
    add_job = MagicMock()
    monkeypatch.setattr(scheduling.app_config, "discord_enabled", True)
    monkeypatch.setattr(scheduling.common.jobqueue, "add_onetime_job", add_job)
    monkeypatch.setattr(scheduling.common.jobqueue, "get_job", lambda _: object())
    scheduling._retry_discord_job("job", scheduling._scheduled_discord_text_job, [], 0)
    add_job.assert_not_called()
    monkeypatch.setattr(scheduling.common.jobqueue, "get_job", lambda _: None)
    scheduling._retry_discord_job("job", scheduling._scheduled_discord_text_job, [], 3)
    add_job.assert_not_called()


@pytest.mark.asyncio
async def test_ready_job_delivers_saved_recipient_mentions(monkeypatch):
    class Channel(discord.abc.Messageable):
        send = AsyncMock()

        async def _get_channel(self):
            return self

    channel = Channel()
    client = SimpleNamespace(get_channel=lambda _: channel)
    monkeypatch.setattr(scheduling, "_ready_discord_client", AsyncMock(return_value=client))
    await scheduling._scheduled_discord_text_job(2, [3, 4], "Reminder")
    assert channel.send.await_args.args == ("<@3> <@4> Reminder",)
