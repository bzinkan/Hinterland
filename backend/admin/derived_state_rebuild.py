"""Process queued per-user derived-state rebuild jobs.

Run as an Azure Container Apps Job::

    python -m admin.derived_state_rebuild
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.db import models
from app.derived_state import process_rebuild_job

log = structlog.get_logger()

_MAX_PER_RUN = 50
_RUNNING_LEASE = timedelta(minutes=15)


async def run(sessions: async_sessionmaker[AsyncSession]) -> tuple[int, int]:
    stale_before = datetime.now(UTC) - _RUNNING_LEASE
    async with sessions() as session:
        ids = (
            (
                await session.execute(
                    select(models.DerivedStateRebuild.id)
                    .where(
                        or_(
                            models.DerivedStateRebuild.status.in_(("queued", "failed")),
                            (
                                (models.DerivedStateRebuild.status == "running")
                                & (models.DerivedStateRebuild.started_at < stale_before)
                            ),
                        ),
                        models.DerivedStateRebuild.attempt_count < 5,
                    )
                    .order_by(models.DerivedStateRebuild.created_at)
                    .limit(_MAX_PER_RUN)
                )
            )
            .scalars()
            .all()
        )
        await session.rollback()

    succeeded = 0
    failed = 0
    for job_id in ids:
        async with sessions() as session:
            if await process_rebuild_job(session, job_id=job_id):
                succeeded += 1
            else:
                failed += 1

    log.info(
        "derived_state_rebuild.run_complete",
        candidates=len(ids),
        succeeded=succeeded,
        failed=failed,
    )
    return succeeded, failed


async def main() -> None:
    settings = get_settings()
    engine = create_async_engine(settings.sqlalchemy_database_url)
    sessions: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine,
        expire_on_commit=False,
    )
    try:
        succeeded, failed = await run(sessions)
        print(f"derived_state_rebuild: {succeeded} succeeded, {failed} failed")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
