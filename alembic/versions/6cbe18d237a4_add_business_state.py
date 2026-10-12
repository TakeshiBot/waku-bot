"""Add durable Business dialog state and owner action claims."""

import sqlalchemy as sa

from alembic import op

revision = "6cbe18d237a4"
down_revision = "a6b7c8d9e0f1"
branch_labels = None
depends_on = None


def upgrade():
    tables = sa.inspect(op.get_bind()).get_table_names()
    if "business_state" not in tables:
        _create_state()
    if "business_action" not in tables:
        _create_actions()


def _create_state():
    op.create_table(
        "business_state",
        sa.Column("owner_id", sa.BigInteger(), nullable=False),
        sa.Column("peer_id", sa.BigInteger(), nullable=False),
        sa.Column("state", sa.JSON(), nullable=False),
        sa.Column("state_version", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("owner_id", "peer_id"),
    )


def _create_actions():
    op.create_table(
        "business_action",
        sa.Column("token", sa.String(48), nullable=False),
        sa.Column("owner_id", sa.BigInteger(), nullable=False),
        sa.Column("connection_id", sa.String(255), nullable=False),
        sa.Column("operation", sa.String(48), nullable=False),
        sa.Column("arguments", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("expires_at", sa.Float(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("token"),
    )
    op.create_index("ix_business_action_owner_id", "business_action", ["owner_id"])


def downgrade():
    op.drop_index("ix_business_action_owner_id", table_name="business_action")
    op.drop_table("business_action")
    op.drop_table("business_state")
