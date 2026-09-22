"""Tenant identity resolution.

This is the ONE place in the application that reads the ``X-Tenant-Id``
header. Every other layer -- services, repositories, SQL -- receives an
already-validated :class:`TenantContext`, never a raw header value. That
separation is what stops tenant handling from being copy-pasted (and
subtly gotten wrong) in every route.

Take-home simplification: tenant identity is a plain, unsigned header.
Any caller can claim to be any tenant by setting ``X-Tenant-Id`` to a
different slug -- there is no authentication behind it. That is explicitly
allowed by the assignment for a take-home. What IS enforced is what a
caller can do *once* a tenant identity is accepted: row-level security in
Postgres (see app/db/pool.py) makes it structurally impossible for a
request scoped to tenant A to read or write tenant B's rows, regardless of
IDs guessed or query bugs written. Swapping this header for a real signed
session/API key later is a change to this file only.
"""

from dataclasses import dataclass
from uuid import UUID

from fastapi import Header, HTTPException, Request


@dataclass(frozen=True)
class TenantContext:
    """A validated tenant identity, safe to pass into services/repositories."""

    id: UUID
    slug: str


async def get_tenant_context(
    request: Request,
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
) -> TenantContext:
    if not x_tenant_id:
        raise HTTPException(status_code=401, detail="X-Tenant-Id header is required")

    pool = request.app.state.pool
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, slug FROM tenants WHERE slug = $1",
            x_tenant_id,
        )

    if row is None:
        # Unknown tenant. Deliberately the same shape of error as a missing
        # header -- we don't want to reveal which tenant slugs exist.
        raise HTTPException(status_code=401, detail="Unknown tenant")

    return TenantContext(id=row["id"], slug=row["slug"])
