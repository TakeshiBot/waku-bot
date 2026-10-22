"""Persist mirror requests and notification delivery claims."""

import sqlalchemy as sa

from alembic import op

revision = "f4b97e18ca20"
down_revision = "e8b2174c6a90"
branch_labels = None
depends_on = None


def upgrade():
    if sa.inspect(op.get_bind()).has_table("mirror_task"):
        return
    op.create_table(
        "mirror_task",
        sa.Column("id", sa.String(32), primary_key=True),
        *(
            sa.Column(name, sa.BigInteger(), nullable=False)
            for name in (
                "origin_chat_id",
                "origin_message_id",
                "requester_id",
                "mirror_chat_id",
                "mirror_bot_id",
            )
        ),
        sa.Column("command_message_id", sa.BigInteger(), nullable=True),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        *(
            sa.Column(stage + "_notification", sa.String(24), nullable=False)
            for stage in ("accepted", "terminal")
        ),
        *(
            sa.Column(stage + "_claimed_at", sa.Float(), nullable=True)
            for stage in ("accepted", "terminal")
        ),
        *(
            sa.Column(stage + "_message_id", sa.BigInteger(), nullable=True)
            for stage in ("accepted", "terminal")
        ),
        sa.Column("last_status_query_at", sa.Float(), nullable=True),
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
    )


def downgrade():
    op.drop_table("mirror_task")
