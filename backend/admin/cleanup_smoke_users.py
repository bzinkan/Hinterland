"""Cleanup task: delete accumulated `smoke+*@dragonfly-test.invalid` users.

scripts/smoke_phase4.py creates a parent + a kid in Firebase Auth and the
corresponding rows in Postgres on every run. This admin task deletes both
sides:

1. Lists all Firebase users; filters to ones with email matching
   `smoke+*@dragonfly-test.invalid` AND any anonymous-ish kid users whose
   `parent_id` custom claim points at one of those parents.
2. Looks up the corresponding `users` rows by `firebase_uid`.
3. Deletes (in order, to satisfy FK constraints):
   - `memberships` referencing those user IDs (kid + parent rows)
   - `groups` owned by those user IDs
   - the `users` rows themselves (kids first since they have a
     `parent_user_id` FK to parents)
4. Deletes the Firebase users.

Run as a Cloud Run Job using the same image as `dragonfly-api`. Same env
vars as the runtime service for DB connection (DRAGONFLY_DATABASE_*).
ADC provides Firebase Admin SDK auth.

Idempotent: re-running with no smoke users to delete is a no-op.
"""

from __future__ import annotations

import asyncio
import re

import structlog
from firebase_admin import auth as firebase_auth
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.auth import _firebase_app
from app.core.config import get_settings
from app.db import models

log = structlog.get_logger()

# Match what scripts/smoke_phase4.py generates plus a couple of forgiving
# variants in case the convention drifts.
SMOKE_EMAIL_RE = re.compile(r"^smoke\+\d+@dragonfly-test\.invalid$", re.IGNORECASE)


def list_smoke_firebase_uids(app: object) -> tuple[list[str], dict[str, str]]:
    """Return (smoke parent UIDs, {kid_uid: parent_uid} via custom claim)."""
    parent_uids: list[str] = []
    kid_to_parent: dict[str, str] = {}

    page = firebase_auth.list_users(app=app)
    while page:
        for user in page.users:
            if user.email and SMOKE_EMAIL_RE.match(user.email):
                parent_uids.append(user.uid)
        page = page.get_next_page()

    if not parent_uids:
        return parent_uids, kid_to_parent

    page = firebase_auth.list_users(app=app)
    while page:
        for user in page.users:
            claims = user.custom_claims or {}
            parent_id = claims.get("parent_id")
            role = claims.get("role")
            if role == "kid" and isinstance(parent_id, str):
                # parent_id custom claim is the Postgres users.id, not
                # the Firebase uid. Need to resolve via the DB pass below.
                kid_to_parent[user.uid] = parent_id
        page = page.get_next_page()

    return parent_uids, kid_to_parent


async def cleanup() -> None:
    settings = get_settings()
    app = _firebase_app(settings)

    parent_firebase_uids, kid_to_parent_pg_id = list_smoke_firebase_uids(app)
    log.info(
        "cleanup_smoke.discovered",
        parent_firebase_uids=len(parent_firebase_uids),
        kid_to_parent_claims=len(kid_to_parent_pg_id),
    )

    if not parent_firebase_uids:
        log.info("cleanup_smoke.nothing_to_do")
        return

    engine = create_async_engine(settings.sqlalchemy_database_url)
    sessions: async_sessionmaker[AsyncSession] = async_sessionmaker(engine, expire_on_commit=False)

    async with sessions() as session:
        # Resolve the Postgres users.id for the smoke parents.
        parent_rows = (
            await session.execute(
                select(models.User.id, models.User.firebase_uid).where(
                    models.User.firebase_uid.in_(parent_firebase_uids)
                )
            )
        ).all()
        parent_pg_ids = [r.id for r in parent_rows]
        log.info("cleanup_smoke.parent_pg_ids", count=len(parent_pg_ids))

        # Find kid users whose parent_user_id points at one of the smoke
        # parents (catches any kid created in the dev environment by a
        # smoke parent, even ones not yet seen in Firebase claims).
        kid_pg_ids: list[str] = []
        if parent_pg_ids:
            kid_pg_ids = (
                (
                    await session.execute(
                        select(models.User.id).where(models.User.parent_user_id.in_(parent_pg_ids))
                    )
                )
                .scalars()
                .all()
            )
            log.info("cleanup_smoke.kid_pg_ids", count=len(kid_pg_ids))

        all_user_pg_ids = parent_pg_ids + kid_pg_ids

        if all_user_pg_ids:
            # 1. memberships referencing any of these users
            del_memberships = await session.execute(
                delete(models.Membership).where(models.Membership.user_id.in_(all_user_pg_ids))
            )
            log.info("cleanup_smoke.deleted_memberships", count=del_memberships.rowcount)

            # 2. groups owned by any of these users
            del_groups = await session.execute(
                delete(models.Group).where(models.Group.owner_user_id.in_(all_user_pg_ids))
            )
            log.info("cleanup_smoke.deleted_groups", count=del_groups.rowcount)

            # 3. kid users first (they have FK to parent_user_id)
            if kid_pg_ids:
                del_kids = await session.execute(
                    delete(models.User).where(models.User.id.in_(kid_pg_ids))
                )
                log.info("cleanup_smoke.deleted_kids", count=del_kids.rowcount)

            # 4. parent users
            if parent_pg_ids:
                del_parents = await session.execute(
                    delete(models.User).where(models.User.id.in_(parent_pg_ids))
                )
                log.info("cleanup_smoke.deleted_parents", count=del_parents.rowcount)

            await session.commit()

    await engine.dispose()

    # 5. Delete Firebase users (parents + kids whose claim pointed at smoke
    #    parents -- since we may have deleted Postgres rows for kids without
    #    Firebase claims, also delete kids resolved via the DB pass).
    kid_firebase_uids_via_claims = list(kid_to_parent_pg_id.keys())
    all_firebase_uids = list(set(parent_firebase_uids + kid_firebase_uids_via_claims))

    if all_firebase_uids:
        result = firebase_auth.delete_users(all_firebase_uids, app=app)
        log.info(
            "cleanup_smoke.deleted_firebase_users",
            success=result.success_count,
            failure=result.failure_count,
        )


if __name__ == "__main__":
    asyncio.run(cleanup())
