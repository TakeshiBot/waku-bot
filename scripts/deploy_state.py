"""Read a database schema fingerprint without migrations or bot connections."""

import asyncio
import hashlib
import json
import sys
from pathlib import Path


async def fingerprint():
    from sqlalchemy import inspect, text
    from sqlalchemy.ext.asyncio import create_async_engine

    from waku.config import app_config

    engine = create_async_engine(app_config.db_url)

    def describe(connection):
        inspector = inspect(connection)
        tables = {}
        for name in sorted(inspector.get_table_names()):
            tables[name] = {
                "columns": [
                    {k: str(c.get(k)) for k in ("name", "type", "nullable", "default")}
                    for c in inspector.get_columns(name)
                ],
                "pk": inspector.get_pk_constraint(name),
                "indexes": inspector.get_indexes(name),
                "unique": inspector.get_unique_constraints(name),
                "foreign_keys": inspector.get_foreign_keys(name),
            }
        # No passwords or table data enter the output. Hashing the target also
        # prevents applying a rollback manifest from a different database.
        target = engine.url.render_as_string(hide_password=True)
        revisions = (
            list(
                connection.execute(
                    text("SELECT version_num FROM alembic_version ORDER BY version_num")
                ).scalars()
            )
            if "alembic_version" in tables
            else []
        )
        payload = json.dumps(
            {"target": target, "tables": tables, "revisions": revisions},
            sort_keys=True,
            default=str,
        )
        return hashlib.sha256(payload.encode()).hexdigest()

    try:
        async with asyncio.timeout(20), engine.connect() as connection:
            return await connection.run_sync(describe)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    try:
        print(asyncio.run(fingerprint()))
    except Exception:
        print(
            "Không xác minh được cấu trúc database; không thực hiện rollback.",
            file=sys.stderr,
        )
        sys.exit(1)
