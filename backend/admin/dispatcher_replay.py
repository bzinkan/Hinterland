"""Re-run the dispatcher on observations whose original dispatch never
recorded success.

Per `docs/dispatcher.md` snapshot scenario 11: "API service crashes
after submission transaction but before dispatch; replay recovers."
This admin task IS the replay.

Selects observations with incomplete durable handler runs (older than a small
grace window so we don't race the in-flight create), builds a Context, and
lets the dispatcher resume only pending/failed/blocked handlers.

Same admin-task pattern as cleanup_smoke_users / sweep_stale_reviews:

    python -m admin.dispatcher_replay

Idempotent: re-running with no NULL rows is a no-op. The DexHandler's
`INSERT ... ON CONFLICT DO NOTHING` makes a re-dispatch of an
already-dispatched observation safe even in the corner case where
dispatched_at was never set despite handlers having run.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.db import models
from app.derived_state.rebuild import acquire_user_lock
from app.dispatcher.core import dispatch
from app.dispatcher.registry import HANDLERS
from app.dispatcher.types import Context

log = structlog.get_logger()

# Skip observations younger than this -- avoids racing a request that's
# still mid-create. 2 minutes is plenty for the worst-case dispatcher run.
_GRACE_WINDOW = timedelta(minutes=2)

# Cap a single replay invocation. Phase 11 scale is tiny; this is mostly
# defensive against runaway loops in dev.
_MAX_PER_RUN = 200


async def replay(session: AsyncSession) -> int:
    cutoff = datetime.now(UTC) - _GRACE_WINDOW
    active_rebuild = (
        select(models.DerivedStateRebuild.id)
        .where(
            models.DerivedStateRebuild.user_id == models.Observation.user_id,
            models.DerivedStateRebuild.status.in_(("queued", "running")),
        )
        .exists()
    )

    rows = (
        await session.execute(
            select(models.Observation.id, models.Observation.user_id)
            .where(
                models.Observation.dispatch_status != "complete",
                models.Observation.updated_at < cutoff,
                models.Observation.rejected_at.is_(None),
                ~active_rebuild,
            )
            .order_by(models.Observation.created_at)
            .limit(_MAX_PER_RUN)
        )
    ).all()

    if not rows:
        log.info("dispatcher_replay.nothing_to_do")
        return 0

    replayed = 0
    failed = 0
    deferred_to_rebuild = 0
    skipped_current_state = 0
    for observation_id, user_id in rows:
        try:
            # Recheck after taking the shared per-user lock. This closes the
            # race where a correction/adoption queues a replacement rebuild
            # after the candidate query but before replay dispatch begins.
            await acquire_user_lock(session, str(user_id))
            active_rebuild_id = await session.scalar(
                select(models.DerivedStateRebuild.id)
                .where(
                    models.DerivedStateRebuild.user_id == str(user_id),
                    models.DerivedStateRebuild.status.in_(("queued", "running")),
                )
                .limit(1)
            )
            if active_rebuild_id is not None:
                deferred_to_rebuild += 1
                await session.commit()
                log.info(
                    "dispatcher_replay.deferred_to_rebuild",
                    observation_id=str(observation_id),
                    rebuild_id=active_rebuild_id,
                )
                continue

            # Candidate rows were read before this user lock. A rebuild may
            # have queued *and completed* in that gap, leaving no active job.
            # Reload every context row under the lock and overwrite any
            # identity-map snapshot before deciding whether dispatch is safe.
            current = (
                await session.execute(
                    select(models.Observation, models.User, models.Group, models.Photo)
                    .join(models.User, models.Observation.user_id == models.User.id)
                    .join(models.Group, models.Observation.group_id == models.Group.id)
                    .join(models.Photo, models.Observation.photo_id == models.Photo.id)
                    .where(
                        models.Observation.id == str(observation_id),
                        models.Observation.user_id == str(user_id),
                    )
                    .execution_options(populate_existing=True)
                )
            ).one_or_none()
            if current is None:
                skipped_current_state += 1
                await session.commit()
                continue

            observation, user, group, photo = current
            if (
                observation.dispatch_status == "complete"
                or observation.rejected_at is not None
                or observation.moderation_status == "rejected"
                or photo.status == "deleted"
                or photo.attachment_status == "deleted"
            ):
                skipped_current_state += 1
                await session.commit()
                log.info(
                    "dispatcher_replay.skipped_current_state",
                    observation_id=observation.id,
                    dispatch_status=observation.dispatch_status,
                    moderation_status=observation.moderation_status,
                    photo_status=photo.status,
                )
                continue

            ctx = Context(
                db=session,
                user=user,
                group=group,
                observation=observation,
                photo=photo,
            )
            await dispatch(ctx, HANDLERS)
            if observation.dispatch_status == "complete":
                replayed += 1
                log.info("dispatcher_replay.success", observation_id=observation.id)
            else:
                failed += 1
                log.warning(
                    "dispatcher_replay.partial",
                    observation_id=observation.id,
                    dispatch_status=observation.dispatch_status,
                )
        except Exception:
            failed += 1
            log.exception("dispatcher_replay.failed", observation_id=observation.id)
            # Roll back any partial state so the next row gets a clean session.
            await session.rollback()

    log.info(
        "dispatcher_replay.complete",
        candidates=len(rows),
        replayed=replayed,
        failed=failed,
        deferred_to_rebuild=deferred_to_rebuild,
        skipped_current_state=skipped_current_state,
    )
    return replayed


async def main() -> None:
    settings = get_settings()
    engine = create_async_engine(settings.sqlalchemy_database_url)
    sessions: async_sessionmaker[AsyncSession] = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as session:
            count = await replay(session)
        print(f"dispatcher_replay: {count} observation(s) re-dispatched")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
