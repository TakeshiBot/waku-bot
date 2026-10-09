"""add affection histogram

Revision ID: a1b2c3d4e5f6
Revises: d327932d860e
Create Date: 2025-12-25 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy import text

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: str | None = "d327932d860e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def affection_bucket(x: int) -> int:
    """
    Map affection values to buckets, matching waku/database/affection.py.
    """
    if x < -200:
        return x // 50
    if x < 200:
        return x // 2
    if x < 500:
        return 100 + (x - 200) // 5
    if x < 1000:
        return 160 + (x - 500) // 10
    if x < 2000:
        return 210 + (x - 1000) // 20
    return 260 + (x - 2000) // 50


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    insp = sa.inspect(bind)
    dialect = bind.dialect.name

    table_name = "affection_histogram"

    # Check whether the table already exists.
    if not insp.has_table(table_name):
        op.create_table(
            table_name,
            sa.Column("bucket", sa.Integer(), primary_key=True, autoincrement=False),
            sa.Column("cnt", sa.BigInteger(), nullable=False, default=0),
        )

        # Create the index.
        op.create_index(
            "ix_affection_histogram_bucket",
            table_name,
            ["bucket"],
            unique=False,
        )

    # Nonlinear bucketing requires processing each row in the application.
    # Use the same Python logic for every database backend.
    if not insp.has_table("user_data"):
        result = []  # A fresh database has no existing values to aggregate.
    elif dialect == "postgresql":
        result = bind.execute(
            text(
                "SELECT config->>'affection' as affection FROM user_data WHERE config->>'affection' IS NOT NULL"
            )
        )
    elif dialect == "mysql":
        result = bind.execute(
            text(
                "SELECT JSON_EXTRACT(config, '$.affection') as affection FROM user_data WHERE JSON_EXTRACT(config, '$.affection') IS NOT NULL"
            )
        )
    else:  # sqlite
        result = bind.execute(
            text(
                "SELECT json_extract(config, '$.affection') as affection FROM user_data WHERE json_extract(config, '$.affection') IS NOT NULL"
            )
        )

    bucket_counts: dict[int, int] = {}
    for row in result:
        affection = int(row[0]) if row[0] is not None else 41
        bucket = affection_bucket(affection)
        bucket_counts[bucket] = bucket_counts.get(bucket, 0) + 1

    # Insert the histogram data.
    if bucket_counts:
        for bucket, cnt in bucket_counts.items():
            if dialect == "postgresql":
                bind.execute(
                    text("""
                        INSERT INTO affection_histogram (bucket, cnt)
                        VALUES (:bucket, :cnt)
                        ON CONFLICT (bucket) DO UPDATE SET cnt = EXCLUDED.cnt
                    """),
                    {"bucket": bucket, "cnt": cnt},
                )
            elif dialect == "mysql":
                bind.execute(
                    text("""
                        INSERT INTO affection_histogram (bucket, cnt)
                        VALUES (:bucket, :cnt)
                        ON DUPLICATE KEY UPDATE cnt = VALUES(cnt)
                    """),
                    {"bucket": bucket, "cnt": cnt},
                )
            else:  # sqlite
                bind.execute(
                    text("""
                        INSERT OR REPLACE INTO affection_histogram (bucket, cnt)
                        VALUES (:bucket, :cnt)
                    """),
                    {"bucket": bucket, "cnt": cnt},
                )

    # PostgreSQL: install the SQL bucketing function and triggers.
    if dialect == "postgresql":
        # Create the SQL version of affection_bucket.
        op.execute(
            text("""
            CREATE OR REPLACE FUNCTION affection_bucket(x INT)
            RETURNS INT AS $$
            BEGIN
                IF x < -200 THEN
                    RETURN x / 50;
                ELSIF x < 200 THEN
                    RETURN x / 2;
                ELSIF x < 500 THEN
                    RETURN 100 + (x - 200) / 5;
                ELSIF x < 1000 THEN
                    RETURN 160 + (x - 500) / 10;
                ELSIF x < 2000 THEN
                    RETURN 210 + (x - 1000) / 20;
                ELSE
                    RETURN 260 + (x - 2000) / 50;
                END IF;
            END;
            $$ LANGUAGE plpgsql IMMUTABLE;
        """)
        )

        # Create the trigger function.
        op.execute(
            text("""
            CREATE OR REPLACE FUNCTION update_affection_histogram()
            RETURNS trigger AS $$
            DECLARE
                old_affection INT;
                new_affection INT;
                old_bucket INT;
                new_bucket INT;
            BEGIN
                IF TG_OP = 'INSERT' THEN
                    new_affection := COALESCE((NEW.config->>'affection')::int, 41);
                    new_bucket := affection_bucket(new_affection);

                    INSERT INTO affection_histogram (bucket, cnt)
                    VALUES (new_bucket, 1)
                    ON CONFLICT (bucket)
                    DO UPDATE SET cnt = affection_histogram.cnt + 1;

                ELSIF TG_OP = 'UPDATE' THEN
                    old_affection := COALESCE((OLD.config->>'affection')::int, 41);
                    new_affection := COALESCE((NEW.config->>'affection')::int, 41);
                    old_bucket := affection_bucket(old_affection);
                    new_bucket := affection_bucket(new_affection);

                    IF old_bucket != new_bucket THEN
                        UPDATE affection_histogram
                        SET cnt = cnt - 1
                        WHERE bucket = old_bucket;

                        INSERT INTO affection_histogram (bucket, cnt)
                        VALUES (new_bucket, 1)
                        ON CONFLICT (bucket)
                        DO UPDATE SET cnt = affection_histogram.cnt + 1;
                    END IF;

                ELSIF TG_OP = 'DELETE' THEN
                    old_affection := COALESCE((OLD.config->>'affection')::int, 41);
                    old_bucket := affection_bucket(old_affection);

                    UPDATE affection_histogram
                    SET cnt = cnt - 1
                    WHERE bucket = old_bucket;
                END IF;

                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;
        """)
        )

        op.execute(
            text("""
            DROP TRIGGER IF EXISTS trg_update_affection_histogram ON user_data;
        """)
        )

        op.execute(
            text("""
            CREATE TRIGGER trg_update_affection_histogram
            AFTER INSERT OR UPDATE OF config OR DELETE ON user_data
            FOR EACH ROW
            EXECUTE FUNCTION update_affection_histogram();
        """)
        )


def downgrade() -> None:
    """Downgrade schema."""
    bind = op.get_bind()
    dialect = bind.dialect.name

    # PostgreSQL: remove the triggers and functions.
    if dialect == "postgresql":
        op.execute(
            text("DROP TRIGGER IF EXISTS trg_update_affection_histogram ON user_data;")
        )
        op.execute(text("DROP FUNCTION IF EXISTS update_affection_histogram;"))
        op.execute(text("DROP FUNCTION IF EXISTS affection_bucket;"))

    # Remove the index and table.
    op.drop_index("ix_affection_histogram_bucket", table_name="affection_histogram")
    op.drop_table("affection_histogram")
