"""Auto-reject review_queue rows still pending after the staleness window.

Per docs/moderation.md "Teacher review lifecycle":

> Stale (no decision in 30 days). The nightly sweep auto-rejects the
> review and runs the rejection path.

Same admin-task pattern as cleanup_smoke_users.py and rarity_refresh.py:

    python -m admin.sweep_stale_reviews

Idempotent: re-running with no stale rows is a no-op. The
photos.status='deleted' update + memberships.observation_count
decrement mirrors what the manual reject endpoint
(POST /v1/review-queue/{id}/reject) does.

Run as a Container Apps Job on the Azure schedule. Document the cron in the
runbook when it is provisioned.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.db import models
from app.moderation.review_service import ReviewResolutionConflict, reject_review_item

log = structlog.get_logger()

# 30 days matches docs/moderation.md. Tunable via this constant rather
# than a config var because the threshold is a moderation-policy
# decision, not an operations one.
_STALE_AFTER = timedelta(days=30)


async def sweep(session: AsyncSession) -> int:
    """Auto-reject stale pending reviews. Returns the count resolved."""
    cutoff = datetime.now(UTC) - _STALE_AFTER

    stale = (
        (
            await session.execute(
                select(models.ReviewQueueItem).where(
                    models.ReviewQueueItem.status == "pending",
                    models.ReviewQueueItem.created_at < cutoff,
                )
            )
        )
        .scalars()
        .all()
    )

    if not stale:
        log.info("sweep_stale_reviews.nothing_to_do")
        return 0

    now = datetime.now(UTC)
    resolved = 0
    for review in stale:
        try:
            await reject_review_item(
                session,
                review=review,
                reviewer_user_id=None,
                nonblocking=True,
            )
        except ReviewResolutionConflict:
            # Another reviewer/sweeper won after the candidate read. Release
            # this transaction's advisory lock and move on.
            await session.rollback()
            continue

        log.info(
            "sweep_stale_reviews.auto_rejected",
            review_id=review.id,
            photo_id=review.photo_id,
            age_days=(now - review.created_at).days,
        )
        await session.commit()
        resolved += 1

    log.info("sweep_stale_reviews.complete", count=resolved)
    return resolved


async def main() -> None:
    settings = get_settings()
    engine = create_async_engine(settings.sqlalchemy_database_url)
    sessions: async_sessionmaker[AsyncSession] = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as session:
            count = await sweep(session)
        print(f"sweep_stale_reviews: {count} review(s) auto-rejected")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
