"""Store Business owner notes and reminder delivery claims."""

import sqlalchemy as sa

from alembic import op

revision = "ab8d20f6a731"
down_revision = "6cbe18d237a4"
branch_labels = None
depends_on = None


def upgrade():
    if sa.inspect(op.get_bind()).has_table("business_note"):
        return
    op.create_table(
        "business_note",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("owner_id", sa.BigInteger(), nullable=False),
        sa.Column("peer_id", sa.BigInteger(), nullable=False),
        sa.Column("connection_id", sa.String(255), nullable=False),
        sa.Column("source_message_id", sa.BigInteger(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("contact", sa.JSON(), nullable=False),
        sa.Column("remind_at", sa.Float(), nullable=True),
        sa.Column("notification_status", sa.String(24), nullable=False),
        sa.Column("reminder_status", sa.String(24), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
    )
    for key in ("owner_id", "notification_status", "reminder_status"):
        op.create_index("ix_business_note_" + key, "business_note", [key])


def downgrade():
    op.drop_table("business_note")
