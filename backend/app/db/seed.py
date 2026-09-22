"""Dev-only seed data.

Not a schema migration -- this is fixture data for local development and
tests, so it's kept separate and only ever runs when APP_ENV=development.
"""

import asyncpg

DEV_TENANTS = [
    ("tenant-a", "Tenant A"),
    ("tenant-b", "Tenant B"),
]


async def ensure_dev_tenants(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        for slug, name in DEV_TENANTS:
            await conn.execute(
                """
                INSERT INTO tenants (slug, name) VALUES ($1, $2)
                ON CONFLICT (slug) DO NOTHING
                """,
                slug,
                name,
            )
