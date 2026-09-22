"""Proves the tenant boundary is enforced by the database, not by
convention. Each test targets one of the ways a leak could actually
happen: reading another tenant's row by a guessed/known id, listing and
getting back rows that don't belong to you, and a connection that skips
the tenant-scoping helper entirely.
"""

import pytest

from app.db.pool import tenant_connection
from app.repositories.notes import NoteRepository


async def test_rls_is_enabled_and_forced_on_notes(pool):
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname = 'notes'"
        )
    assert row["relrowsecurity"] is True, "RLS must be enabled on every tenant-owned table"
    assert row["relforcerowsecurity"] is True, "RLS must be forced, or the owning app role bypasses it"


async def test_tenant_cannot_get_another_tenants_note_by_id(pool, tenant_a, tenant_b):
    async with tenant_connection(pool, tenant_a) as conn:
        note = await NoteRepository(conn, tenant_a).create(title="tenant-a-secret")

    # Tenant B knows the exact id (e.g. guessed, leaked in a log, whatever)
    # and asks for it through their own tenant-scoped repository.
    async with tenant_connection(pool, tenant_b) as conn:
        result = await NoteRepository(conn, tenant_b).get(note.id)

    # Not-found, not a 403 -- we don't want to confirm the row exists at all.
    assert result is None


async def test_tenant_list_never_includes_another_tenants_rows(pool, tenant_a, tenant_b):
    async with tenant_connection(pool, tenant_a) as conn:
        await NoteRepository(conn, tenant_a).create(title="a-only")

    async with tenant_connection(pool, tenant_b) as conn:
        await NoteRepository(conn, tenant_b).create(title="b-only")
        b_notes = await NoteRepository(conn, tenant_b).list()

    assert {n.title for n in b_notes} == {"b-only"}
    assert all(n.tenant_id == tenant_b.id for n in b_notes)


async def test_connection_without_tenant_context_sees_nothing(pool, tenant_a):
    async with tenant_connection(pool, tenant_a) as conn:
        await NoteRepository(conn, tenant_a).create(title="should-not-leak")

    # Simulates a future bug: code that grabs a raw connection instead of
    # going through tenant_connection(). app.tenant_id was never set, so
    # current_setting(..., true) is NULL and the policy matches no rows --
    # this must fail closed, never fall back to "see everything".
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM notes")

    assert rows == []


async def test_tenant_cannot_insert_a_row_for_another_tenant(pool, tenant_a, tenant_b):
    async with tenant_connection(pool, tenant_a) as conn:
        # Even if application code had a bug and passed tenant_b's id
        # explicitly, the RLS WITH CHECK clause rejects a row whose
        # tenant_id doesn't match this session's tenant.
        with pytest.raises(Exception):  # noqa: B017 - asyncpg raises PostgresError subclasses
            await conn.execute(
                "INSERT INTO notes (tenant_id, title) VALUES ($1, $2)",
                tenant_b.id,
                "forged",
            )
