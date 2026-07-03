"""Re-run the dispatcher on observations whose original dispatch never
recorded success.

Per `docs/dispatcher.md` snapshot scenario 11: "API service crashes
after submission transaction but before dispatch; replay recovers."
This admin task IS the replay.

Selects observations with `dispatched_at IS NULL` (older than a small
grace window so we don't race the in-flight create), builds a Context
for each, runs `dispatch()`, stamps `dispatched_at` on success.

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
from app.dispatcher.core import dispatch
from app.dispatcher.registry import HANDLERS
from app.dispatcher.types import Context

log = structlog.get_logger()

# Skip observations touched within this window -- avoids racing a request
# that's still mid-create OR a first-taxon PATCH that just cleared
# dispatched_at and is about to run its own dispatch (keyed to updated_at,
# not created_at: the live flow assigns the taxon minutes-to-days after
# create, so a created_at grace would never shield the PATCH window).
# 2 minutes is plenty for the worst-case dispatcher run.
_GRACE_WINDOW = timedelta(minutes=2)

# Cap a single replay invocation. Phase 11 scale is tiny; this is mostly
# defensive against runaway loops in dev.
_MAX_PER_RUN = 200


async def replay(session: AsyncSession) -> int:
    cutoff = datetime.now(UTC) - _GRACE_WINDOW

    rows = (
        await session.execute(
            select(models.Observation, models.User, models.Group, models.Photo)
            .join(models.User, models.Observation.user_id == models.User.id)
            .join(models.Group, models.Observation.group_id == models.Group.id)
            .join(models.Photo, models.Observation.photo_id == models.Photo.id)
            .where(
                models.Observation.dispatched_at.is_(None),
                models.Observation.updated_at < cutoff,
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
    for observation, user, group, photo in rows:
        ctx = Context(
            db=session,
            user=user,
            group=group,
            observation=observation,
            photo=photo,
        )
        try:
            await dispatch(ctx, HANDLERS)
            # dispatch() never raises for handler failures (per-handler
            # catch-alls), so an unconditional stamp would permanently
            # end retries for an observation whose Sanctuary write
            # failed transiently -- and replay is the ONLY delivery
            # path for contributions repaired by migration 20260703_0009.
            # Leave the row eligible when world reported failure (or an
            # identified observation somehow has no world result);
            # handlers are per-observation idempotent, so re-running is
            # safe.
            world_result = ctx.results.get("world")
            world_failed = world_result is None or bool(world_result.state.get("error"))
            if world_failed and observation.taxon_id is not None:
                failed += 1
                log.warning(
                    "dispatcher_replay.world_failed_still_eligible",
                    observation_id=observation.id,
                )
                await session.commit()
                continue
            observation.dispatched_at = datetime.now(UTC)
            await session.commit()
            replayed += 1
            log.info("dispatcher_replay.success", observation_id=observation.id)
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
