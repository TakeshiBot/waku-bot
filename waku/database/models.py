from dataclasses import asdict, dataclass, field
from datetime import date, datetime

import sqlalchemy as sa
from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
)


class Base(DeclarativeBase):
    pass


@dataclass
class UserConfig:
    lang: str = "vi"
    affection: int = 0
    coins: int = 144 * 16
    dm_ai_enabled: bool = False

    @classmethod
    def from_dict(cls, data: dict | None) -> "UserConfig":
        if data is None:
            return cls()
        return cls(
            lang=data.get("lang", "vi"),
            coins=data.get("coins", 144 * 16),
            affection=data.get("affection", 0),
            dm_ai_enabled=data.get("dm_ai_enabled", False),
        )

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ChatConfig:
    waifu_enabled: bool = True
    delete_events_enabled: bool = False
    unpin_channel_pin_enabled: bool = False
    quote_probability: float = 0.001
    quote_pin_message: bool = True
    title_permissions: dict | None = None
    greeting: str | None = None
    ai_reply: bool = True
    ai_reply_other_bots_enabled: bool = False
    ai_comment: bool = False
    agent_moderation_enabled: bool = False
    setu_enabled: bool = True
    telegram_r18_mode: int = 0
    convert_b23_enabled: bool = True
    parse_links_enabled: bool = True
    parse_artwork_enabled: bool = True
    parse_sites_enabled: dict[str, bool] = field(default_factory=dict)
    pick_bottle_enabled: bool = True
    group_memory_enabled: bool = True
    sticker_memory_enabled: bool = True
    parse_wechat_enabled: bool = True
    discord_enabled: bool = False
    discord_muted: bool = False
    discord_allow_r18: bool = False
    discord_r18_mode: int = 0
    discord_ai_reply: bool = True
    discord_reply_to_bots: bool = False
    discord_auth_status: str = "none"
    discord_auth_requester_id: int | None = None
    discord_auth_channel_id: int | None = None
    discord_auth_requested_at: str | None = None
    discord_auth_rejection_reason: str | None = None
    discord_auth_review_messages: list[dict] | None = None
    rss_agent_summary: bool = False
    rss_agent_broadcast: bool = False
    verify_enabled: bool = False
    verify_strategy: str = "all"
    verify_method: str = "math_easy"
    verify_max_attempts: int = 3
    verify_timeout_seconds: int = 120
    verify_fail_action: str = "kick"
    verify_questions: list[dict] = field(
        default_factory=list
    )  # [{"question": str, "options": [str], "answers": [str]}]
    lang: str = "vi"

    @classmethod
    def from_dict(cls, data: dict | None) -> "ChatConfig":
        if data is None:
            return cls()
        return cls(
            waifu_enabled=data.get("waifu_enabled", True),
            delete_events_enabled=data.get("delete_events_enabled", False),
            unpin_channel_pin_enabled=data.get("unpin_channel_pin_enabled", False),
            quote_probability=data.get("quote_probability", 0.001),
            quote_pin_message=data.get("quote_pin_message", False),
            title_permissions=data.get("title_permissions", {}),
            greeting=data.get("greeting", None),
            ai_reply=data.get("ai_reply", True),
            ai_reply_other_bots_enabled=data.get("ai_reply_other_bots_enabled", False),
            setu_enabled=data.get("setu_enabled", True),
            telegram_r18_mode=data.get("telegram_r18_mode", 0),
            convert_b23_enabled=data.get("convert_b23_enabled", False),
            parse_links_enabled=data.get("parse_links_enabled", True),
            parse_artwork_enabled=data.get("parse_artwork_enabled", True),
            parse_sites_enabled=data.get("parse_sites_enabled") or {},
            pick_bottle_enabled=data.get("pick_bottle_enabled", True),
            ai_comment=data.get("ai_comment", False),
            agent_moderation_enabled=data.get("agent_moderation_enabled", False) is True,
            group_memory_enabled=data.get("group_memory_enabled", True),
            sticker_memory_enabled=data.get("sticker_memory_enabled", True),
            parse_wechat_enabled=data.get("parse_wechat_enabled", True),
            discord_enabled=data.get("discord_enabled", False),
            discord_muted=data.get("discord_muted", False),
            discord_allow_r18=data.get("discord_allow_r18", False),
            discord_r18_mode=data.get(
                "discord_r18_mode", 2 if data.get("discord_allow_r18", False) else 0
            ),
            discord_ai_reply=data.get("discord_ai_reply", True),
            discord_reply_to_bots=data.get("discord_reply_to_bots", False) is True,
            discord_auth_status=data.get("discord_auth_status", "none"),
            discord_auth_requester_id=data.get("discord_auth_requester_id"),
            discord_auth_channel_id=data.get("discord_auth_channel_id"),
            discord_auth_requested_at=data.get("discord_auth_requested_at"),
            discord_auth_rejection_reason=data.get("discord_auth_rejection_reason"),
            discord_auth_review_messages=data.get("discord_auth_review_messages"),
            rss_agent_summary=data.get("rss_agent_summary", False),
            rss_agent_broadcast=data.get("rss_agent_broadcast", False),
            verify_enabled=data.get("verify_enabled", False),
            verify_strategy=data.get("verify_strategy", "all"),
            verify_method=data.get("verify_method", "math_easy"),
            verify_max_attempts=data.get("verify_max_attempts", 3),
            verify_timeout_seconds=data.get("verify_timeout_seconds", 120),
            verify_fail_action=data.get("verify_fail_action", "kick"),
            verify_questions=data.get("verify_questions") or [],
            lang=data.get("lang", "vi"),
        )

    def to_dict(self) -> dict:
        return asdict(self)


class GroupModerationState(Base):
    """Durable AI moderation state; user_id=0 stores group-wide snapshots."""

    __tablename__ = "group_moderation_state"

    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    state: Mapped[dict] = mapped_column(JSON, default=dict)
    state_version: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class UserChatAssociation(Base):
    __tablename__ = "user_chat_association"

    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("user_data.id", ondelete="CASCADE"),
        primary_key=True,
        index=True,
    )
    chat_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("chat_data.id", ondelete="CASCADE"),
        primary_key=True,
        index=True,
    )

    waifu_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("user_data.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    is_bot_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    promoted_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )


class UserData(Base):
    __tablename__ = "user_data"

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=False,
        index=True,
    )

    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    full_name: Mapped[str] = mapped_column(String(256), nullable=False)

    avatar_big_id: Mapped[str | None] = mapped_column(
        String(256),
        nullable=True,
    )

    config: Mapped[dict] = mapped_column(
        JSON,
        default=lambda: asdict(UserConfig()),
    )
    is_married: Mapped[bool] = mapped_column(Boolean, default=False)
    married_waifu_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("user_data.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    waifu_mention: Mapped[bool] = mapped_column(Boolean, default=False)

    is_bot: Mapped[bool] = mapped_column(Boolean, default=False)
    is_real_user: Mapped[bool] = mapped_column(Boolean, default=True)
    is_bot_global_admin: Mapped[bool] = mapped_column(Boolean, default=False)

    is_blocked: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )
    update_avatar_at: Mapped[datetime | None] = mapped_column(
        DateTime(),
        nullable=True,
        default=None,
    )

    chats: Mapped[list["ChatData"]] = relationship(
        "ChatData",
        secondary="user_chat_association",
        back_populates="members",
        primaryjoin="UserData.id == UserChatAssociation.user_id",
        secondaryjoin="ChatData.id == UserChatAssociation.chat_id",
        lazy="noload",
    )

    quotes: Mapped[list["Quote"]] = relationship(
        "Quote",
        back_populates="user",
        cascade="all, delete-orphan",
        lazy="noload",
    )

    married_waifu: Mapped["UserData | None"] = relationship(
        "UserData",
        remote_side=[id],
        post_update=True,
    )

    @property
    def user_config(self) -> UserConfig:
        return UserConfig.from_dict(self.config)

    @user_config.setter
    def user_config(self, config: UserConfig) -> None:
        self.config = config.to_dict()

    def __repr__(self) -> str:
        return f"<UserData(id={self.id}, username='{self.username}', full_name='{self.full_name}')>"


class ChatData(Base):
    __tablename__ = "chat_data"

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=False,
        index=True,
    )

    title: Mapped[str] = mapped_column(String(256), nullable=False)
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)

    is_blocked: Mapped[bool] = mapped_column(Boolean, default=False)

    config: Mapped[dict] = mapped_column(
        JSON,
        default=lambda: asdict(ChatConfig()),
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    members: Mapped[list["UserData"]] = relationship(
        "UserData",
        secondary="user_chat_association",
        back_populates="chats",
        primaryjoin="ChatData.id == UserChatAssociation.chat_id",
        secondaryjoin="UserData.id == UserChatAssociation.user_id",
        lazy="noload",
    )

    quotes: Mapped[list["Quote"]] = relationship(
        "Quote",
        back_populates="chat",
        cascade="all, delete-orphan",
        lazy="noload",
    )

    @property
    def chat_config(self) -> ChatConfig:
        return ChatConfig.from_dict(self.config)

    @chat_config.setter
    def chat_config(self, config: ChatConfig) -> None:
        self.config = config.to_dict()

    def __repr__(self) -> str:
        return f"<ChatData(id={self.id}, title='{self.title}', username='{self.username}')>"


class DiscordChatData(Base):
    """Discord-only guild/DM settings; IDs never share Telegram storage."""

    __tablename__ = "discord_chat_data"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    config: Mapped[dict] = mapped_column(JSON, default=lambda: asdict(ChatConfig()))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    @property
    def chat_config(self) -> ChatConfig:
        return ChatConfig.from_dict(self.config)

    @chat_config.setter
    def chat_config(self, config: ChatConfig) -> None:
        self.config = config.to_dict()


class Quote(Base):
    __tablename__ = "quotes"

    link: Mapped[str] = mapped_column(String(256), primary_key=True)

    chat_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("chat_data.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("user_data.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    qer_id: Mapped[int] = mapped_column(
        BigInteger,
        index=True,
    )

    message_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    text: Mapped[str | None] = mapped_column(String(4096), nullable=True)
    img: Mapped[str | None] = mapped_column(String(256), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    user: Mapped["UserData"] = relationship(
        foreign_keys=[user_id],
        back_populates="quotes",
        lazy="noload",
    )
    chat: Mapped["ChatData"] = relationship(
        "ChatData",
        back_populates="quotes",
        lazy="noload",
    )

    def __repr__(self) -> str:
        return f"<Quote(link='{self.link}', chat_id={self.chat_id}, user_id={self.user_id})>"


class Bottle(Base):
    __tablename__ = "bottles"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
        index=True,
    )

    sender_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("user_data.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    text: Mapped[str] = mapped_column(String(4096), nullable=True)
    picks: Mapped[int] = mapped_column(BigInteger, default=0)
    reports: Mapped[int] = mapped_column(BigInteger, default=0)
    file_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    media_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    """
    meida_type can be one of the following:
    - image
    - video
    - audio
    - document
    - voice
    - None (for text-only bottles)
    """
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    last_picked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
    )

    def __repr__(self) -> str:
        return f"<Bottle(id={self.id}, sender_id={self.sender_id})>"


class BottleReply(Base):
    __tablename__ = "bottle_replies"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
        index=True,
    )
    bottle_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("bottles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    replier_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("user_data.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    text: Mapped[str] = mapped_column(String(4096), nullable=False)
    is_anonymous: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=sa.text("false")
    )
    file_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    media_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    def __repr__(self) -> str:
        return f"<BottleReply(id={self.id}, bottle_id={self.bottle_id}, replier_id={self.replier_id})>"


@dataclass
class ChatPolicy:
    """Per-chat settings the operator controls, as opposed to the group's admins.

    Deliberately separate from `ChatConfig`, which is what `/config` and the group
    settings page write: that document is saved wholesale by anyone who can manage
    the bot in the chat, so an operator-only field living there would be clobbered
    by the next group-admin save.

    Adding a field here needs no migration - the column is JSON and `from_dict`
    supplies the default for rows written before the field existed. That is the
    point of the shape: the next "which groups may do X" question is a field, not
    a table.
    """

    # Whether the AI agent may act here, when agent_whitelist_mode is on.
    agent_allowed: bool = False
    # Whether RSS subscriptions may be created here, when rss_whitelist_mode is on.
    rss_allowed: bool = False
    # Daily shared free token pool for the group account; 0 = unallocated.
    # The group pool follows personal free tokens and serves anonymous admins and channel identities.
    agent_quota_daily_tokens: int = 0
    # Whether the group is fully exempt from billing (no counters or limits).
    agent_quota_exempt: bool = False

    @classmethod
    def from_dict(cls, data: dict | None) -> "ChatPolicy":
        if data is None:
            return cls()
        return cls(
            agent_allowed=data.get("agent_allowed", False),
            rss_allowed=data.get("rss_allowed", False),
            agent_quota_daily_tokens=data.get("agent_quota_daily_tokens", 0),
            agent_quota_exempt=data.get("agent_quota_exempt", False),
        )

    def to_dict(self) -> dict:
        return asdict(self)


class ChatPolicyData(Base):
    """Operator-controlled policy for one chat.

    A row exists only for chats an operator has actually made a decision about, so
    the table stays small and its absence is meaningful: no row means every policy
    is at its default.

    There is no FK to `chat_data`. An operator can grant a group access before the
    bot has ever seen a message in it, and a chat being purged from `chat_data`
    should not silently revoke a decision that was made deliberately. `chat_title`
    is a denormalised copy, kept only so the panel can label a row for a chat that
    is not in `chat_data` yet.
    """

    __tablename__ = "chat_policy"

    chat_id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=False,
        index=True,
    )
    # Nullable: a row added by id alone has no title until the bot sees the chat.
    chat_title: Mapped[str | None] = mapped_column(String(256), nullable=True)

    policy: Mapped[dict] = mapped_column(
        JSON,
        default=lambda: asdict(ChatPolicy()),
    )

    # Who last changed it, for the audit trail. Not an FK, as above.
    updated_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    note: Mapped[str | None] = mapped_column(String(256), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    @property
    def chat_policy(self) -> ChatPolicy:
        return ChatPolicy.from_dict(self.policy)

    @chat_policy.setter
    def chat_policy(self, policy: ChatPolicy) -> None:
        self.policy = policy.to_dict()

    def __repr__(self) -> str:
        return f"<ChatPolicyData(chat_id={self.chat_id}, policy={self.policy})>"


class VerificationSession(Base):
    """An active new-member verification session."""

    __tablename__ = "verification_sessions"
    __table_args__ = (
        UniqueConstraint(
            "chat_id", "user_id", name="uq_verification_sessions_chat_user"
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True, index=True
    )
    chat_id: Mapped[int] = mapped_column(BigInteger, index=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    method: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict] = mapped_column(JSON)
    challenge_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    attempts_left: Mapped[int] = mapped_column(Integer, default=3)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"<VerificationSession(id={self.id}, chat_id={self.chat_id}, user_id={self.user_id})>"


class VerificationMember(Base):
    """A verified group member; the first_message policy skips repeat verification."""

    __tablename__ = "verification_members"

    chat_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=False
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=False
    )
    verified_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"<VerificationMember(chat_id={self.chat_id}, user_id={self.user_id})>"


class Gift(Base):
    __tablename__ = "gifts"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
        index=True,
    )
    owner_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("user_data.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    rarity: Mapped[int] = mapped_column(Integer, nullable=False)
    sent_to_bot: Mapped[bool] = mapped_column(Boolean, default=False)

    gift_id: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    def __repr__(self) -> str:
        return (
            f"<Gift(id={self.id}, owner_id={self.owner_id}, gift_id='{self.gift_id}')>"
        )


class RssFeed(Base):
    """One remote feed, fetched once however many chats subscribe to it.

    Deduplicated by URL: polling the same feed once per subscriber would multiply
    outbound requests by the subscriber count for identical bytes.

    `seen_entry_ids` is the newest-N entry ids from the last successful fetch, and it
    is how "new" is decided. Timestamps are not usable for this: `published_parsed`
    is missing or wrong on a large share of real feeds, and a feed that republishes
    with a bumped date would re-push every entry. An id set has no such failure mode.
    """

    __tablename__ = "rss_feeds"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True, index=True
    )
    url: Mapped[str] = mapped_column(String(1024), nullable=False, unique=True)
    # Feed's self-reported title, shown in listings. None until the first fetch.
    title: Mapped[str | None] = mapped_column(String(512), nullable=True)

    # Conditional-GET state, replayed as request headers on the next poll.
    etag: Mapped[str | None] = mapped_column(String(256), nullable=True)
    last_modified: Mapped[str | None] = mapped_column(String(256), nullable=True)

    # Entry ids already delivered; see the class docstring.
    seen_entry_ids: Mapped[list] = mapped_column(JSON, default=list)

    last_fetched_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    # Last failure text, cleared on success. Surfaced in /rss list and the panel so a
    # dead feed is visible instead of silently never pushing.
    last_error: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # Consecutive failures. At >= MAX_FAILURES the feed is skipped by the poll job.
    failure_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=sa.text("0")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"<RssFeed(id={self.id}, url='{self.url}')>"


class RssSubscription(Base):
    """One chat's subscription to one feed.

    No FK to `chat_data`: like `chat_policy`, a subscription may be created for a chat
    before the bot has any row for it, and purging a chat should not silently drop a
    deliberate subscription. The push job resolves the chat id against Telegram anyway.
    """

    __tablename__ = "rss_subscriptions"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True, index=True
    )
    feed_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("rss_feeds.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    # Who created it, for the audit trail. Not an FK, as above.
    created_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    paused: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=sa.text("false")
    )
    # Per-subscription poll interval in minutes; None means "follow the global
    # rss_interval". The feed's effective interval is the minimum across its
    # unpaused subscriptions.
    interval_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    feed: Mapped[RssFeed] = relationship(lazy="joined")

    __table_args__ = (
        sa.UniqueConstraint("chat_id", "feed_id", name="uq_rss_subscription_chat_feed"),
    )

    def __repr__(self) -> str:
        return f"<RssSubscription(chat_id={self.chat_id}, feed_id={self.feed_id})>"


class AgentPersistentFile(Base):
    """One file the agent chose to persist for a chat.

    The payload lives in Telegram chat history (a document message sent by
    the bot); this row records where, so the agent can list, re-fetch and
    overwrite files by name. Deleting the row never deletes the message: the
    file stays in chat history, it just leaves the agent's managed set.
    """

    __tablename__ = "agent_persistent_files"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True, index=True
    )
    # Scope: group chats share their files, private chats are user-scoped.
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    description: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    # The document message sent to the chat and the download credential.
    tg_message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    file_id: Mapped[str] = mapped_column(String(512), nullable=False)
    file_unique_id: Mapped[str] = mapped_column(String(512), nullable=False)
    file_name: Mapped[str | None] = mapped_column(String(512), nullable=True)
    mime_type: Mapped[str | None] = mapped_column(String(256), nullable=True)
    file_size: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        sa.UniqueConstraint("chat_id", "name", name="uq_agent_persistent_chat_name"),
    )

    def __repr__(self) -> str:
        return f"<AgentPersistentFile(chat_id={self.chat_id}, name={self.name!r})>"


class AgentUsageDaily(Base):
    """A quota account's usage for one UTC date, measured in tokens.

    `input_tokens` / `output_tokens` track actual daily model usage; `free_used_tokens` tracks
    the part paid from this account's free allocation. Calls paid by a group pool or credits still
    count toward personal usage, so the panel can report both total usage and remaining free tokens.
    """

    __tablename__ = "agent_usage_daily"

    # "user" = speaker account; "chat" = conversation/group account.
    scope: Mapped[str] = mapped_column(String(16), primary_key=True)
    scope_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=False
    )
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    # Keep server_default aligned with migrations so create_all produces the same DDL.
    requests: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=sa.text("0")
    )
    free_used_tokens: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=sa.text("0")
    )
    input_tokens: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=sa.text("0")
    )
    output_tokens: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=sa.text("0")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (sa.Index("ix_agent_usage_daily_day", "day"),)

    def __repr__(self) -> str:
        return (
            f"<AgentUsageDaily({self.scope}:{self.scope_id}, day={self.day}, "
            f"requests={self.requests}, free_used_tokens={self.free_used_tokens})>"
        )


class AgentCredit(Base):
    """An account's token balance; absent rows mean zero, created on the first credit grant.

    Balances may become negative: usage is known only after a run, so overspending is recorded as debt.
    The next preflight rejects calls until an operator grants credit; no nonnegative constraint applies.
    """

    __tablename__ = "agent_credits"

    scope: Mapped[str] = mapped_column(String(16), primary_key=True)
    scope_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=False
    )
    balance: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=sa.text("0")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:
        return f"<AgentCredit({self.scope}:{self.scope_id}, balance={self.balance})>"


class AgentCreditLedger(Base):
    """An append-only token balance ledger.

    Balances alone do not show provenance. Every grant or charge appends a ledger row in the same transaction.
    For future payments, `ref` stores an external reference unique per account to deduplicate callbacks.
    `reason` is "admin" (panel grant/adjustment) or "usage" (usage charge).
    """

    __tablename__ = "agent_credit_ledger"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True, index=True
    )
    scope: Mapped[str] = mapped_column(String(16), nullable=False)
    scope_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # Negative amounts are charges, positive amounts are grants; this is the actual applied change.
    delta: Mapped[int] = mapped_column(BigInteger, nullable=False)
    balance_after: Mapped[int] = mapped_column(BigInteger, nullable=False)
    reason: Mapped[str] = mapped_column(String(32), nullable=False)
    ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        sa.UniqueConstraint(
            "scope", "scope_id", "ref", name="uq_agent_credit_ledger_scope_ref"
        ),
        sa.Index("ix_agent_credit_ledger_account", "scope", "scope_id"),
    )


class AgentRun(Base):
    """One agent run: one conversation turn or one bot-initiated auxiliary call.

    Written once when the run ends - there is no "running" row - so a crash leaves
    no trace. Run-level counters come from the run's own usage, while
    `parent_run_id` links a nested run (compaction, transcription) to the turn that
    spawned it. No foreign key: the id is a plain column, like the quota tables.
    """

    __tablename__ = "agent_runs"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True, index=True
    )
    # One of waku.database.agent_trace.RUN_KINDS.
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    # One of RUN_STATUSES.
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    # Only set when status is "rejected": one of REJECT_REASONS.
    reject_reason: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # This conversation's instance id: one random value per (chat, user) thread,
    # replaced when that thread starts over (/forget) or the process restarts. Runs
    # with no conversation of their own (RSS work, sticker descriptions) leave it
    # null; nested runs (compaction, transcription) inherit their parent's.
    # Indexed: cleanup groups by it and the capture side looks it up every turn.
    session_id: Mapped[str | None] = mapped_column(
        String(32), nullable=True, index=True
    )
    chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    parent_run_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    model_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # One of "main" / "multimodal" / "small" / "struct" / "transcribe".
    model_role: Mapped[str | None] = mapped_column(String(16), nullable=True)
    streaming: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=sa.text("false")
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    finished_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    duration_ms: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=sa.text("0")
    )
    requests: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=sa.text("0")
    )
    tool_calls: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=sa.text("0")
    )
    input_tokens: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=sa.text("0")
    )
    output_tokens: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=sa.text("0")
    )
    cache_read_tokens: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=sa.text("0")
    )
    cache_write_tokens: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=sa.text("0")
    )
    # One of "str" / "end_turn" / "ask_user" / "none".
    output_kind: Mapped[str | None] = mapped_column(String(16), nullable=True)
    output_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Length of the untruncated output, so a shortened row is still measurable.
    output_chars: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_class: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    event_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=sa.text("0")
    )
    events_dropped: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=sa.text("0")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        sa.Index("ix_agent_runs_started_at", "started_at"),
        sa.Index("ix_agent_runs_chat_started", "chat_id", "started_at"),
        sa.Index("ix_agent_runs_user_started", "user_id", "started_at"),
        sa.Index("ix_agent_runs_status_started", "status", "started_at"),
        sa.Index("ix_agent_runs_kind_started", "kind", "started_at"),
    )

    def __repr__(self) -> str:
        return (
            f"<AgentRun(id={self.id}, kind={self.kind!r}, status={self.status!r}, "
            f"chat_id={self.chat_id}, user_id={self.user_id})>"
        )


class AgentRunEvent(Base):
    """One step of a run: a model request/response, a tool call/result, and so on.

    `payload` holds the step's own data. For `model_request` it is an increment:
    only the messages past the longest common prefix with the previous request of
    the same conversation are stored, and the instructions only when they changed,
    which is what keeps a conversation at roughly one copy of its history instead of
    one per request. Over-long strings are cut to `agent_trace_max_field_chars`
    before storage.
    """

    __tablename__ = "agent_run_events"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True, index=True
    )
    run_id: Mapped[int] = mapped_column(Integer, nullable=False)
    # 1-based, monotonic within the run; a call and its return take one seq each.
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    # One of EVENT_KINDS.
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="ok", server_default=sa.text("'ok'")
    )
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Character count of the payload before truncation.
    payload_chars: Mapped[int | None] = mapped_column(Integer, nullable=True)
    truncated: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=sa.text("false")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        sa.UniqueConstraint("run_id", "seq", name="uq_agent_run_events_run_seq"),
    )

    def __repr__(self) -> str:
        return (
            f"<AgentRunEvent(run_id={self.run_id}, seq={self.seq}, "
            f"kind={self.kind!r}, name={self.name!r})>"
        )
