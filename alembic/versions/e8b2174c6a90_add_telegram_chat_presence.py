"""Track Telegram membership and Guest chats in separate namespaces."""

import sqlalchemy as sa

from alembic import op

revision = "e8b2174c6a90"
down_revision = "c9d3e4f5a6b7"
branch_labels = None
depends_on = None


def upgrade():
    if sa.inspect(op.get_bind()).has_table("telegram_chat_presence"):
        return
    op.create_table(
        "telegram_chat_presence",
        sa.Column("scope", sa.String(16), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("title", sa.String(256), nullable=False),
        sa.Column("username", sa.String(64), nullable=True),
        sa.Column("is_member", sa.Boolean(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("scope", "chat_id"),
    )


def downgrade():
    op.drop_table("telegram_chat_presence")
