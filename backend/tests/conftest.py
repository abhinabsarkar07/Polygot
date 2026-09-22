import asyncpg
import pytest_asyncio

from app.core.config import Settings
from app.core.tenant import TenantContext
from app.db.migrate import run_migrations
from app.db.seed import ensure_dev_tenants


@pytest_asyncio.fixture
async def pool():
    settings = Settings()
    p = await asyncpg.create_pool(settings.database_url, min_size=1, max_size=5)
    await run_migrations(p)
    await ensure_dev_tenants(p)
    yield p
    await p.close()


@pytest_asyncio.fixture(autouse=True)
async def _clean_notes_between_tests(pool):
    yield
    async with pool.acquire() as conn:
        await conn.execute("TRUNCATE notes")


async def _tenant_by_slug(pool: asyncpg.Pool, slug: str) -> TenantContext:
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT id, slug FROM tenants WHERE slug = $1", slug)
    assert row is not None, f"dev tenant '{slug}' was not seeded"
    return TenantContext(id=row["id"], slug=row["slug"])


@pytest_asyncio.fixture
async def tenant_a(pool) -> TenantContext:
    return await _tenant_by_slug(pool, "tenant-a")


@pytest_asyncio.fixture
async def tenant_b(pool) -> TenantContext:
    return await _tenant_by_slug(pool, "tenant-b")
