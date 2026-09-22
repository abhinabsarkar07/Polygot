"""A deliberately tiny migration runner.

We use plain, numbered .sql files instead of a migration framework
(Alembic etc.) -- for a one-day project with a handful of tables, a
framework buys nothing and adds a dependency the whole team has to learn.
Every applied filename is recorded in ``schema_migrations`` so re-running
this at startup is a no-op after the first time.
"""

import logging
from pathlib import Path

import asyncpg

logger = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


async def run_migrations(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version TEXT PRIMARY KEY,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )

        applied = {r["version"] for r in await conn.fetch("SELECT version FROM schema_migrations")}

        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if path.name in applied:
                continue
            sql = path.read_text(encoding="utf-8")
            async with conn.transaction():
                await conn.execute(sql)
                await conn.execute(
                    "INSERT INTO schema_migrations (version) VALUES ($1)", path.name
                )
            logger.info("applied migration %s", path.name)
