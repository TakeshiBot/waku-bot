"""Bot-wide role commands are restricted to authorized, personal Telegram DMs."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pyrogram.enums import ChatType

from waku.plugins.groupmanage import botadmin


@pytest.fixture
def env(monkeypatch):
    actor = SimpleNamespace(id=10, is_bot=False)
    actor_record = SimpleNamespace(is_bot_global_admin=True, is_bot=False)
    target = SimpleNamespace(is_bot_global_admin=False, is_bot=False)
    records = {10: actor_record, 20: target}
    message = SimpleNamespace(
        from_user=actor,
        chat=SimpleNamespace(id=10, type=ChatType.PRIVATE),
        command=["botpromote", "20"],
        business_connection_id=None,
        reply_text=AsyncMock(),
    )
    monkeypatch.setattr(botadmin.app_config, "owners", [1])
    monkeypatch.setattr(botadmin, "_ROLE_LOCK", asyncio.Lock())
    lookup = AsyncMock(side_effect=lambda user_id: records.get(user_id))
    grant = AsyncMock()
    monkeypatch.setattr(botadmin.database, "get_user_by_id", lookup)
    monkeypatch.setattr(botadmin.database, "set_user_global_admin", grant)
    monkeypatch.setattr(
        botadmin.database,
        "get_user_config",
        AsyncMock(return_value=SimpleNamespace(lang="vi")),
    )
    return SimpleNamespace(
        message=message,
        actor=actor,
        actor_record=actor_record,
        target=target,
        records=records,
        grant=grant,
        lookup=lookup,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "command,was_admin,desired",
    [
        ("botpromote", False, True),
        ("botdemote", True, False),
        ("botpromote@waku_bot", False, True),
    ],
)
async def test_admin_dm_explicit_id_updates_only_global_role(
    env, command, was_admin, desired
):
    env.message.command = [command, "20"]
    env.target.is_bot_global_admin = was_admin
    await botadmin.set_user_bot_admin(None, env.message)
    env.grant.assert_awaited_once_with(20, desired)
    assert "20" in env.message.reply_text.call_args.args[0]


@pytest.mark.asyncio
async def test_owner_can_grant_without_database_admin_flag(env):
    env.actor.id = env.message.chat.id = 1
    await botadmin.set_user_bot_admin(None, env.message)
    env.grant.assert_awaited_once_with(20, True)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scenario",
    ["group", "nonadmin", "blocked", "bot", "no_actor", "business", "wrong_dm"],
)
async def test_other_contexts_cannot_modify_roles(env, scenario):
    if scenario == "group":
        env.message.chat = SimpleNamespace(id=-100123, type=ChatType.SUPERGROUP)
    elif scenario == "nonadmin":
        env.actor_record.is_bot_global_admin = False
    elif scenario == "blocked":
        env.actor_record.is_blocked = True
    elif scenario == "bot":
        env.actor.is_bot = True
    elif scenario == "no_actor":
        env.message.from_user = None
    elif scenario == "business":
        env.message.business_connection_id = "business-connection"
    else:
        env.message.chat.id = 12
    await botadmin.set_user_bot_admin(None, env.message)
    env.grant.assert_not_awaited()
    env.message.reply_text.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "args",
    [
        ["botpromote"],
        ["botpromote", "@user"],
        ["botpromote", "-20"],
        ["botpromote", "20", "extra"],
        ["botpromote", "２０"],
        ["botpromote", "0"],
        ["botpromote", str(2**63)],
        ["botpromote", "9" * 5000],
        ["botpromote", "10"],
        ["botdemote", "1"],
    ],
)
async def test_ids_must_be_numeric_and_not_protected(env, args):
    env.message.command = args
    await botadmin.set_user_bot_admin(None, env.message)
    env.grant.assert_not_awaited()
    env.message.reply_text.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("target_state", ["unknown", "bot", "already"])
async def test_no_unknown_bot_or_redundant_grants(env, target_state):
    if target_state == "unknown":
        env.records.pop(20)
    elif target_state == "bot":
        env.target.is_bot = True
    else:
        env.target.is_bot_global_admin = True
    await botadmin.set_user_bot_admin(None, env.message)
    env.grant.assert_not_awaited()
    env.message.reply_text.assert_awaited_once()


@pytest.mark.asyncio
async def test_queued_command_rechecks_revoked_authority(env):
    async with botadmin._ROLE_LOCK:
        task = asyncio.create_task(botadmin.set_user_bot_admin(None, env.message))
        await asyncio.sleep(0)
        env.actor_record.is_bot_global_admin = False
    await task
    env.grant.assert_not_awaited()
    env.message.reply_text.assert_not_awaited()
