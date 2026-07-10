"""15-min cron job that re-enqueues stuck `inat_submit_outbox` rows.

Closes the recovery half of the Risk 0002 transactional outbox pattern.
The producer side (moderation worker clean path / review-queue approve
handler) inserts an `inat_submit_outbox` row in the SAME transaction
that flips `observations.moderation_status` to `clean`, commits, then
attempts to enqueue to the Azure Service Bus `inat-submit` queue. If
that enqueue fails -- transient Service Bus outage, namespace not
provisioned yet during the Stream B rollout window, supply-chain
regression dropping `azure-servicebus`, anything else -- the row stays
in `pending` with `last_attempt_at` set and `retry_count` bumped.

This job picks up those `pending` rows past a 5-minute grace window
and re-attempts the enqueue. Cap 200/run defends against runaway
loops in dev; production traffic at beta scale is well under that.

Idempotent under replay. Re-running the job with no `pending` rows
past the grace is a no-op. The 5-min grace prevents racing the
producer's own first-attempt path.

Invocation::

    python -m admin.inat_outbox_replay

Same admin-task pattern as `dispatcher_replay.py` /
`sweep_stale_reviews.py`:

- `async def main()` builds Settings + engine + sessionmaker, then
  hands a session to `replay()`.
- `replay()` does the work and returns the count of rows it acted on.
- Tests stub `replay()` against an `AsyncMock(AsyncSession)`.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import Settings, get_settings
from app.db import models
from app.inat.enqueue import enqueue_inat_submit

log = structlog.get_logger()

# Grace window before re-enqueueing. Producers attempt the enqueue
# inline after their commit; this gives that path room to complete
# before we second-guess it. The Service Bus consumer's lock duration
# is ~30s, so 5 minutes is comfortably past any natural retry window.
_GRACE_WINDOW = timedelta(minutes=5)

# Cap a single replay invocation. Phase 11 scale is tiny; this is mostly
# defensive against runaway loops in dev.
_MAX_PER_RUN = 200


async def replay(session: AsyncSession, settings: Settings) -> int:
    """Re-enqueue every `pending` outbox row past the grace window.

    Returns the number of rows that were successfully re-enqueued (i.e.
    flipped from `pending` to `enqueued`). Rows whose enqueue still
    fails stay in `pending` with `retry_count` + `last_error` bumped.
    """
    if not settings.inat_submit_enabled:
        log.info("inat_outbox_replay.disabled")
        return 0

    cutoff = datetime.now(UTC) - _GRACE_WINDOW

    rows = (
        (
            await session.execute(
                select(models.InatSubmitOutbox)
                .where(
                    models.InatSubmitOutbox.status == "pending",
                    # Either never attempted (last_attempt_at IS NULL) OR
                    # last attempt is older than the grace window. NULL-safe
                    # comparison via OR keeps the producer's first attempt
                    # from racing this job.
                    (
                        models.InatSubmitOutbox.last_attempt_at.is_(None)
                        | (models.InatSubmitOutbox.last_attempt_at < cutoff)
                    ),
                )
                .order_by(models.InatSubmitOutbox.created_at)
                .limit(_MAX_PER_RUN)
            )
        )
        .scalars()
        .all()
    )

    if not rows:
        log.info("inat_outbox_replay.nothing_to_do")
        return 0

    enqueued = 0
    still_pending = 0
    now = datetime.now(UTC)
    for row in rows:
        result = await enqueue_inat_submit(row.observation_id, settings=settings)
        if result.success:
            await session.execute(
                update(models.InatSubmitOutbox)
                .where(models.InatSubmitOutbox.observation_id == row.observation_id)
                .values(status="enqueued", last_attempt_at=now)
            )
            await session.commit()
            enqueued += 1
            log.info("inat_outbox_replay.enqueued", observation_id=row.observation_id)
        else:
            await session.execute(
                update(models.InatSubmitOutbox)
                .where(models.InatSubmitOutbox.observation_id == row.observation_id)
                .values(
                    last_attempt_at=now,
                    retry_count=models.InatSubmitOutbox.retry_count + 1,
                    last_error=result.reason or "unknown",
                )
            )
            await session.commit()
            still_pending += 1
            log.info(
                "inat_outbox_replay.still_pending",
                observation_id=row.observation_id,
                reason=result.reason,
                retry_count=row.retry_count + 1,
            )

    log.info(
        "inat_outbox_replay.complete",
        candidates=len(rows),
        enqueued=enqueued,
        still_pending=still_pending,
    )
    return enqueued


async def main() -> None:
    settings = get_settings()
    engine = create_async_engine(settings.sqlalchemy_database_url)
    sessions: async_sessionmaker[AsyncSession] = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as session:
            count = await replay(session, settings)
        print(f"inat_outbox_replay: {count} row(s) re-enqueued")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
