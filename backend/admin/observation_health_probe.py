"""Emit database-backed Observation health signals for Azure Monitor.

The scheduled job is read-only.  It turns otherwise hard-to-alert relational
invariants into one structured ``observation.ops_probe`` event every five
minutes.  Azure scheduled-query alerts consume these numeric fields.
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings

log = structlog.get_logger()


@dataclass(frozen=True)
class ObservationHealth:
    stale_moderation_outbox: int
    stale_pending_photos: int
    stale_dispatch_runs: int
    stale_rebuilds: int
    failed_rebuilds: int
    state_mismatches: int

    @property
    def healthy(self) -> bool:
        return not any(asdict(self).values())


_PROBE_SQL = text(
    """
    SELECT
      (
        SELECT count(*)
        FROM moderation_outbox
        WHERE status IN ('pending', 'failed', 'processing')
          AND created_at < :moderation_cutoff
      ) AS stale_moderation_outbox,
      (
        SELECT count(*)
        FROM observations
        WHERE rejected_at IS NULL
          AND moderation_status IN ('pending', 'processing', 'failed')
          AND created_at < :pending_photo_cutoff
      ) AS stale_pending_photos,
      (
        SELECT count(*)
        FROM observations
        WHERE rejected_at IS NULL
          AND dispatch_status IN ('pending', 'partial', 'unverified')
          AND created_at < :dispatch_cutoff
      ) AS stale_dispatch_runs,
      (
        SELECT count(*)
        FROM derived_state_rebuilds
        WHERE status IN ('queued', 'running')
          AND created_at < :rebuild_cutoff
      ) AS stale_rebuilds,
      (
        SELECT count(*)
        FROM derived_state_rebuilds
        WHERE status = 'failed' AND attempt_count >= 5
      ) AS failed_rebuilds,
      ((
        SELECT count(*)
        FROM observations AS observation
        JOIN photos AS photo ON photo.id = observation.photo_id
        WHERE (
          observation.rejected_at IS NULL
          AND observation.moderation_status IN (
            'pending', 'processing', 'clean', 'quarantine', 'failed'
          )
          AND photo.attachment_status <> 'attached'
        ) OR (
          observation.moderation_status = 'clean' AND photo.status <> 'clean'
        ) OR (
          observation.moderation_status = 'quarantine' AND photo.status <> 'quarantine'
        ) OR (
          observation.moderation_status = 'pilot_private'
          AND NOT (
            (photo.status = 'pending' AND photo.attachment_status = 'attached')
            OR (photo.status = 'deleted' AND photo.attachment_status = 'deleted')
          )
        ) OR (
          observation.moderation_status = 'rejected'
          AND (photo.status <> 'deleted' OR photo.attachment_status <> 'deleted')
        )
      ) + (
        SELECT count(*)
        FROM photos AS photo
        LEFT JOIN observations AS observation ON observation.photo_id = photo.id
        WHERE photo.attachment_status = 'attached' AND observation.id IS NULL
      ) + (
        SELECT count(*)
        FROM observations AS observation
        LEFT JOIN moderation_outbox AS outbox
          ON outbox.observation_id = observation.id
        WHERE observation.rejected_at IS NULL
          AND observation.moderation_status IN ('pending', 'processing', 'failed')
          AND outbox.observation_id IS NULL
      ) + (
        SELECT count(*)
        FROM moderation_outbox AS outbox
        JOIN observations AS observation ON observation.id = outbox.observation_id
        WHERE outbox.status = 'succeeded'
          AND observation.moderation_status IN ('pending', 'processing', 'failed')
      )) AS state_mismatches
    """
)


async def probe(
    session: AsyncSession,
    *,
    now: datetime | None = None,
) -> ObservationHealth:
    current = now or datetime.now(UTC)
    row = (
        (
            await session.execute(
                _PROBE_SQL,
                {
                    "moderation_cutoff": current - timedelta(minutes=10),
                    "pending_photo_cutoff": current - timedelta(hours=1),
                    "dispatch_cutoff": current - timedelta(minutes=10),
                    "rebuild_cutoff": current - timedelta(minutes=15),
                },
            )
        )
        .mappings()
        .one()
    )
    health = ObservationHealth(
        stale_moderation_outbox=int(row["stale_moderation_outbox"]),
        stale_pending_photos=int(row["stale_pending_photos"]),
        stale_dispatch_runs=int(row["stale_dispatch_runs"]),
        stale_rebuilds=int(row["stale_rebuilds"]),
        failed_rebuilds=int(row["failed_rebuilds"]),
        state_mismatches=int(row["state_mismatches"]),
    )
    log.info("observation.ops_probe", healthy=health.healthy, **asdict(health))
    return health


async def main() -> None:
    settings = get_settings()
    engine = create_async_engine(settings.sqlalchemy_database_url)
    sessions: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine,
        expire_on_commit=False,
    )
    try:
        async with sessions() as session:
            health = await probe(session)
        print(
            "observation_health_probe: "
            f"healthy={str(health.healthy).lower()} "
            f"stale_moderation_outbox={health.stale_moderation_outbox} "
            f"stale_pending_photos={health.stale_pending_photos} "
            f"stale_dispatch_runs={health.stale_dispatch_runs} "
            f"stale_rebuilds={health.stale_rebuilds} "
            f"failed_rebuilds={health.failed_rebuilds} "
            f"state_mismatches={health.state_mismatches}"
        )
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
