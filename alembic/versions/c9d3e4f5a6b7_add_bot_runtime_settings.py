"""Store global runtime feature values in the database."""

import sqlalchemy as sa

from alembic import op

revision = "c9d3e4f5a6b7"
down_revision = "ab8d20f6a731"
branch_labels = None
depends_on = None


def upgrade():
    if sa.inspect(op.get_bind()).has_table("bot_runtime_setting"):
        return
    op.create_table(
        "bot_runtime_setting",
        sa.Column("key", sa.String(128), primary_key=True),
        sa.Column("value", sa.JSON(), nullable=False),
    )


def downgrade():
    op.drop_table("bot_runtime_setting")
