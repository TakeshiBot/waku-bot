"""Private AI preference and entry-point contracts without bot startup or services."""

import ast
import asyncio
from dataclasses import asdict, dataclass
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pyrogram
import pytest
import sqlalchemy
import sqlalchemy.dialects.mysql
import sqlalchemy.dialects.postgresql
import sqlalchemy.dialects.sqlite
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from waku.i18n import i18n
from waku.webapp.schemas import MeConfigPatch

ROOT = Path(__file__).resolve().parents[1]


def load_definitions(path, names, namespace):
    tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
    definitions = [
        node for node in ast.walk(tree) if getattr(node, "name", None) in names
    ]
    assert {node.name for node in definitions} == names
    for node in definitions:
        node.decorator_list = [
            decorator
            for decorator in node.decorator_list
            if isinstance(decorator, ast.Name) and decorator.id == "dataclass"
        ]
    future = ast.ImportFrom(
        module="__future__", names=[ast.alias(name="annotations")], level=0
    )
    module = ast.fix_missing_locations(
        ast.Module(body=[future, *definitions], type_ignores=[])
    )
    exec(compile(module, path, "exec"), namespace)


@pytest.fixture
def user_config():
    namespace = {"dataclass": dataclass, "asdict": asdict}
    load_definitions("waku/database/models.py", {"UserConfig"}, namespace)
    return namespace["UserConfig"]


def test_legacy_user_config_defaults_off_and_roundtrips_other_preferences(user_config):
    for legacy in (None, {}, {"lang": "en", "coins": 900, "affection": 7}):
        assert user_config.from_dict(legacy).dm_ai_enabled is False
    stored = {"lang": "en", "coins": 900, "affection": 7, "dm_ai_enabled": True}
    assert user_config.from_dict(stored).to_dict() == stored


@pytest.fixture
def preferences(user_config):
    class Base(DeclarativeBase):
        pass

    class UserData(Base):
        __tablename__ = "users"
        id: Mapped[int] = mapped_column(primary_key=True)
        config: Mapped[dict] = mapped_column(sqlalchemy.JSON)

    engine = sqlalchemy.create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    connection = engine.connect()
    connection.execute(
        sqlalchemy.insert(UserData).values(
            id=1,
            config={
                "lang": "vi",
                "coins": 123,
                "affection": 45,
                "future_preference": {"keep": True},
            },
        )
    )

    class Session:
        def get_bind(self):
            return engine

        async def execute(self, statement):
            return connection.execute(statement)

        async def get(self, model, user_id, **_kwargs):
            row = connection.execute(
                sqlalchemy.select(model.config).where(model.id == user_id)
            ).first()
            return (
                SimpleNamespace(
                    id=user_id, user_config=user_config.from_dict(row.config)
                )
                if row
                else None
            )

    namespace = {"sqlalchemy": sqlalchemy, "UserData": UserData}
    load_definitions(
        "waku/database/user.py",
        {
            "patch_user_preferences",
            "_patch_user_config_values",
            "update_user_config",
            "add_user_coins",
            "cost_user_coins",
        },
        namespace,
    )
    yield SimpleNamespace(
        patch=namespace["patch_user_preferences"],
        update=namespace["update_user_config"],
        add_coins=namespace["add_user_coins"],
        cost_coins=namespace["cost_user_coins"],
        patch_values=namespace["_patch_user_config_values"],
        session=Session(),
        connection=connection,
        model=UserData,
    )
    connection.close()
    engine.dispose()


async def test_atomic_preference_patch_preserves_balances_unknown_keys_and_dm_flag(
    preferences,
):
    p = preferences
    enabled = await p.patch(1, dm_ai_enabled=True, session=p.session)
    assert enabled.dm_ai_enabled is True
    # A later language edit must not replay an older snapshot with AI disabled.
    changed = await p.patch(1, lang="en", session=p.session)
    assert changed.dm_ai_enabled is True and changed.lang == "en"
    stored = p.connection.execute(sqlalchemy.select(p.model.config)).scalar_one()
    assert stored == {
        "lang": "en",
        "coins": 123,
        "affection": 45,
        "future_preference": {"keep": True},
        "dm_ai_enabled": True,
    }
    disabled = await p.patch(1, dm_ai_enabled=False, session=p.session)
    assert disabled.dm_ai_enabled is False


async def test_preference_patch_rejects_missing_user_and_non_boolean(preferences):
    with pytest.raises(ValueError, match="not found"):
        await preferences.patch(99, dm_ai_enabled=True, session=preferences.session)
    with pytest.raises(TypeError, match="must be bool"):
        await preferences.patch(1, dm_ai_enabled="false", session=preferences.session)


@pytest.mark.parametrize(
    "dialect,expected", [("postgresql", "jsonb_set"), ("mysql", "json_set")]
)
async def test_preference_patch_builds_json_update_for_other_supported_databases(
    preferences, user_config, dialect, expected
):
    db_dialect = getattr(sqlalchemy.dialects, dialect).dialect()
    statements = []

    async def execute(statement):
        statements.append(str(statement.compile(dialect=db_dialect)))
        return SimpleNamespace(rowcount=1)

    session = SimpleNamespace(
        get_bind=lambda: SimpleNamespace(dialect=db_dialect),
        execute=execute,
        get=AsyncMock(
            return_value=SimpleNamespace(user_config=user_config(dm_ai_enabled=True))
        ),
    )
    await preferences.patch(1, lang="en", dm_ai_enabled=True, session=session)
    assert len(statements) == 1
    assert expected in statements[0]
    assert "WHERE users.id" in statements[0]
    if dialect == "postgresql":
        assert "coalesce(nullif(CAST(users.config AS JSONB), CAST(" in statements[0]


@pytest.fixture
def start_ui():
    namespace = {
        "i18n": i18n,
        "enums": pyrogram.enums,
        "InlineKeyboardButton": pyrogram.types.InlineKeyboardButton,
        "InlineKeyboardMarkup": pyrogram.types.InlineKeyboardMarkup,
        "consts": SimpleNamespace(
            REPO_URL="https://example.test/repo", DOCS_URL="https://example.test/docs"
        ),
        "app_config": SimpleNamespace(webapp=False),
        "logger": MagicMock(),
        "database": SimpleNamespace(
            get_user_config=AsyncMock(),
            set_user_dm_ai_enabled=AsyncMock(
                return_value=SimpleNamespace(lang="vi", dm_ai_enabled=True)
            ),
        ),
    }
    load_definitions(
        "waku/plugins/start.py", {"PrivateStartBotMarkup", "toggle_dm_ai"}, namespace
    )
    return SimpleNamespace(**namespace)


def callback(user=7, chat=7, private=True, data="dm_ai:7:1"):
    return SimpleNamespace(
        from_user=SimpleNamespace(id=user),
        data=data,
        answer=AsyncMock(),
        message=SimpleNamespace(
            chat=SimpleNamespace(
                id=chat,
                type=pyrogram.enums.ChatType.PRIVATE
                if private
                else pyrogram.enums.ChatType.SUPERGROUP,
            ),
            edit_reply_markup=AsyncMock(),
        ),
    )


async def test_dm_toggle_is_owner_only_and_repeated_click_is_idempotent(start_ui):
    query = callback(data=b"dm_ai:7:1")
    await start_ui.toggle_dm_ai(None, query)
    await start_ui.toggle_dm_ai(None, query)
    assert [
        call.args for call in start_ui.database.set_user_dm_ai_enabled.await_args_list
    ] == [(7, True), (7, True)]
    markup = query.message.edit_reply_markup.await_args.kwargs["reply_markup"]
    assert markup.inline_keyboard[0][0].callback_data == "dm_ai:7:0"


@pytest.mark.parametrize(
    "query",
    [
        callback(user=8),
        callback(chat=8),
        callback(chat=-8, private=False),
        callback(data="dm_ai:7:invalid"),
    ],
)
async def test_dm_toggle_rejects_other_users_groups_and_malformed_payloads(
    start_ui, query
):
    await start_ui.toggle_dm_ai(None, query)
    start_ui.database.set_user_dm_ai_enabled.assert_not_awaited()
    query.answer.assert_awaited_once()
    assert query.answer.await_args.kwargs["show_alert"] is True


@pytest.mark.parametrize("enabled", [True, False])
def test_start_button_targets_opposite_state(start_ui, enabled):
    markup = start_ui.PrivateStartBotMarkup("vi", enabled, 7).build()
    button = markup.inline_keyboard[0][0]
    assert button.callback_data == f"dm_ai:7:{int(not enabled)}"
    assert button.text == i18n.t(
        "bot.dm.button_enabled" if enabled else "bot.dm.button_disabled", locale="vi"
    )


@pytest.fixture
def entry_points():
    config = SimpleNamespace(lang="vi", dm_ai_enabled=False)
    chat_config = SimpleNamespace(
        lang="vi", ai_reply=True, ai_reply_other_bots_enabled=False
    )
    lock = asyncio.Lock()
    namespace = {
        "asyncio": asyncio,
        "pyrogram": pyrogram,
        "i18n": i18n,
        "app_config": SimpleNamespace(
            agent=True, agent_private_chat_required_channel=None
        ),
        "agent": object(),
        "is_chat_allowed": lambda _chat: True,
        "word_reply": AsyncMock(),
        "logger": MagicMock(),
        "database": SimpleNamespace(
            get_user_config=AsyncMock(return_value=config),
            get_chat_config=AsyncMock(return_value=chat_config),
            get_user_by_id=AsyncMock(return_value=SimpleNamespace(is_bot=False)),
        ),
        "tools": SimpleNamespace(
            is_user_blocked=AsyncMock(return_value=False),
            get_ask_state=AsyncMock(return_value=None),
        ),
        "myfilter": SimpleNamespace(
            mention_me_filter_func=AsyncMock(return_value=False)
        ),
        "quota": SimpleNamespace(
            subject_of=lambda _message: "subject",
            subject_for_chat=lambda *_args: "subject",
            can_start=AsyncMock(return_value=False),
            notify_exhausted=AsyncMock(),
            get_state=AsyncMock(),
        ),
        "trace": SimpleNamespace(note_rejection=AsyncMock()),
        "state": SimpleNamespace(get_conversation_lock=lambda *_args: lock),
        "_is_channel_member": AsyncMock(return_value=True),
        "GROUP_CHAT_TYPES": {
            pyrogram.enums.ChatType.GROUP,
            pyrogram.enums.ChatType.SUPERGROUP,
        },
    }
    load_definitions(
        "waku/plugins/agent/agent.py", {"wake_agent", "_run_agent_for_ask"}, namespace
    )
    message = SimpleNamespace(
        id=3,
        chat=SimpleNamespace(id=7, type=pyrogram.enums.ChatType.PRIVATE),
        from_user=SimpleNamespace(id=7),
        sender_chat=None,
        command=[],
        reply_text=AsyncMock(),
    )
    return SimpleNamespace(
        namespace=namespace, config=config, message=message, lock=lock
    )


@pytest.mark.parametrize("entry", ["wake", "ask"])
async def test_dm_off_never_enters_quota_channel_check_or_model(entry_points, entry):
    e = entry_points
    e.namespace["app_config"].agent_private_chat_required_channel = "@channel"
    if entry == "wake":
        await e.namespace["wake_agent"](None, e.message)
    else:
        await e.namespace["_run_agent_for_ask"](None, e.message, 7, 7, "answer")
    e.namespace["quota"].can_start.assert_not_awaited()
    e.namespace["_is_channel_member"].assert_not_awaited()
    e.message.reply_text.assert_not_awaited()


@pytest.mark.parametrize("entry", ["wake", "ask"])
@pytest.mark.parametrize("private", [True, False])
async def test_dm_enabled_and_group_chat_keep_existing_quota_gate(
    entry_points, entry, private
):
    e = entry_points
    e.config.dm_ai_enabled = private
    if not private:
        e.message.chat = SimpleNamespace(id=-7, type=pyrogram.enums.ChatType.SUPERGROUP)
    if entry == "wake":
        await e.namespace["wake_agent"](None, e.message)
    else:
        await e.namespace["_run_agent_for_ask"](
            None, e.message, 7, e.message.chat.id, "answer"
        )
    e.namespace["quota"].can_start.assert_awaited_once()
    e.namespace["quota"].notify_exhausted.assert_awaited_once()
    assert not e.lock.locked()


async def test_explicit_chat_command_explains_how_to_enable_dm_ai(entry_points):
    e = entry_points
    e.message.command = ["chat", "hello"]
    await e.namespace["wake_agent"](None, e.message)
    e.message.reply_text.assert_awaited_once_with(
        i18n.t("bot.dm.unavailable", locale="vi")
    )
    e.namespace["quota"].can_start.assert_not_awaited()


async def test_private_ask_rejects_chat_id_mismatch(entry_points):
    e = entry_points
    e.config.dm_ai_enabled = True
    await e.namespace["_run_agent_for_ask"](None, e.message, 7, -99, "answer")
    e.namespace["quota"].can_start.assert_not_awaited()


async def test_disable_during_wake_preflight_prevents_new_run(entry_points):
    e = entry_points
    e.namespace["database"].get_user_config.side_effect = [
        SimpleNamespace(lang="vi", dm_ai_enabled=True),
        SimpleNamespace(lang="vi", dm_ai_enabled=False),
    ]
    await e.namespace["wake_agent"](None, e.message)
    e.namespace["quota"].can_start.assert_not_awaited()
    assert not e.lock.locked()


async def test_dm_off_cannot_inject_message_into_an_active_turn(entry_points):
    e = entry_points
    e.namespace["database"].get_user_config.side_effect = [
        SimpleNamespace(lang="vi", dm_ai_enabled=True),
        SimpleNamespace(lang="vi", dm_ai_enabled=False),
    ]
    e.namespace["_queue_interjection"] = AsyncMock()
    await e.lock.acquire()
    try:
        await e.namespace["wake_agent"](None, e.message)
        e.namespace["_queue_interjection"].assert_not_awaited()
    finally:
        e.lock.release()


def test_me_patch_can_disable_ai_without_changing_language():
    patch = MeConfigPatch(dm_ai_enabled=False)
    assert patch.model_dump(exclude_unset=True) == {"dm_ai_enabled": False}
    assert MeConfigPatch(lang="en").dm_ai_enabled is None


async def test_legacy_json_null_config_can_enable_dm_ai(preferences):
    p = preferences
    p.connection.execute(sqlalchemy.insert(p.model).values(id=2, config=None))
    enabled = await p.patch(2, dm_ai_enabled=True, session=p.session)
    assert enabled.dm_ai_enabled is True


async def test_legacy_config_and_coin_writes_preserve_latest_dm_preference(
    preferences, user_config
):
    p = preferences
    snapshot = user_config(lang="vi", coins=123, affection=45)
    await p.patch(1, dm_ai_enabled=True, session=p.session)
    snapshot.coins = 200
    updated = await p.update(1, snapshot, session=p.session)
    assert updated.dm_ai_enabled is True and updated.coins == 200
    added = await p.add_coins(1, 10, session=p.session)
    assert added.dm_ai_enabled is True and added.coins == 210
    spent = await p.cost_coins(1, 10**6, session=p.session)
    assert spent.dm_ai_enabled is True and spent.coins == -144 * 16
    stored = p.connection.execute(sqlalchemy.select(p.model.config)).scalar_one()
    assert stored["future_preference"] == {"keep": True}


@pytest.mark.parametrize("operation", ["gift", "bottle", "waifu", "affection"])
async def test_chat_economy_writes_do_not_replay_dm_flag_from_stale_snapshot(
    preferences, operation
):
    p = preferences
    original_get = p.session.get
    toggled = False

    async def read_then_toggle(model, user_id, **kwargs):
        nonlocal toggled
        snapshot = await original_get(model, user_id, **kwargs)
        if not toggled:
            toggled = True
            # Simulate another request switching AI on after the balance read.
            await p.patch(1, dm_ai_enabled=True, session=p.session)
        return snapshot

    p.session.get = read_then_toggle
    p.session.add = MagicMock()
    namespace = {
        "sqlalchemy": sqlalchemy,
        "UserData": p.model,
        "_patch_user_config_values": p.patch_values,
        "app_config": SimpleNamespace(
            cost_throw_bottle_base=10, cost_user_change_waifu_base=10
        ),
    }
    if operation == "gift":
        namespace.update(
            gift=SimpleNamespace(
                get_gift_by_id=lambda _gift: SimpleNamespace(price=10)
            ),
            add_gift_to_user=AsyncMock(return_value="gift"),
        )
        load_definitions("waku/database/gift.py", {"buy_gift_for_user"}, namespace)
        assert (
            await namespace["buy_gift_for_user"](1, "gift", session=p.session) == "gift"
        )
    elif operation == "bottle":
        namespace["Bottle"] = SimpleNamespace
        load_definitions("waku/database/bottle.py", {"add_bottle"}, namespace)
        await namespace["add_bottle"](1, "content", None, None, session=p.session)
        p.session.add.assert_called_once()
    elif operation == "waifu":
        namespace.update(
            get_association=AsyncMock(return_value=SimpleNamespace(waifu_id=20)),
            take_waifu_for_user_in_chat=AsyncMock(return_value=SimpleNamespace(id=30)),
        )
        load_definitions(
            "waku/database/association.py", {"change_user_waifu_in_chat"}, namespace
        )
        result = await namespace["change_user_waifu_in_chat"](
            1, SimpleNamespace(id=-1), session=p.session
        )
        assert result.id == 30
    else:
        namespace["runtime_config"] = SimpleNamespace(db_is_postgres=False)
        load_definitions(
            "waku/database/affection.py",
            {"update_user_affection", "affection_bucket"},
            namespace,
        )
        await namespace["update_user_affection"](1, 44, session=p.session)
    stored = p.connection.execute(sqlalchemy.select(p.model.config)).scalar_one()
    assert stored["dm_ai_enabled"] is True
    assert stored["future_preference"] == {"keep": True}
    assert stored["coins"] == (123 if operation == "affection" else 113)
    assert stored["affection"] == (44 if operation == "affection" else 45)


async def test_language_only_api_patch_uses_atomic_preferences_without_replaying_snapshot():
    refreshed = SimpleNamespace(id=7)
    database = SimpleNamespace(
        patch_user_preferences=AsyncMock(),
        update_user_config=AsyncMock(),
        get_user_by_id=AsyncMock(return_value=refreshed),
        set_user_waifu_mention=AsyncMock(),
    )
    namespace = {
        "database": database,
        "read_me": AsyncMock(return_value="result"),
        "write_limiter": SimpleNamespace(check=MagicMock()),
        "client_key": lambda *_args: "key",
    }
    load_definitions("waku/webapp/routers/me.py", {"update_my_config"}, namespace)
    user = SimpleNamespace(
        id=7, data=SimpleNamespace(user_config=SimpleNamespace(dm_ai_enabled=False))
    )
    assert (
        await namespace["update_my_config"](None, user, MeConfigPatch(lang="en"))
        == "result"
    )
    database.patch_user_preferences.assert_awaited_once_with(
        7, lang="en", dm_ai_enabled=None
    )
    database.update_user_config.assert_not_awaited()
