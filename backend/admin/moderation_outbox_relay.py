"""Relay committed moderation-outbox rows to Service Bus.

This scheduled recovery path is also the W1 producer. It intentionally reads
only database work created in the same transaction as an attached observation;
BlobCreated events never enter the moderation queue.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import Settings, get_settings
from app.db import models
from app.moderation.enqueue import enqueue_moderation_work

log = structlog.get_logger()

_GRACE_WINDOW = timedelta(seconds=15)
_MAX_PER_RUN = 200
_MAX_RETRIES = 5


async def relay(session: AsyncSession, settings: Settings) -> int:
    if not settings.service_bus_enabled:
        log.info("moderation.outbox_relay.not_configured")
        return 0

    cutoff = datetime.now(UTC) - _GRACE_WINDOW
    rows = (
        await session.execute(
            select(models.ModerationOutbox, models.Photo)
            .join(models.Photo, models.Photo.id == models.ModerationOutbox.photo_id)
            .where(
                models.ModerationOutbox.status.in_(("pending", "failed")),
                # Migration registers legacy pending observations before
                # their old `pending/<id>.jpg` bytes are canonicalized.
                # Never publish raw or unverified child photos; the
                # scheduled legacy reconciler makes these rows eligible.
                models.Photo.attachment_status == "attached",
                models.Photo.canonical_object_name.is_not(None),
                models.Photo.verified_at.is_not(None),
                (
                    models.ModerationOutbox.last_attempt_at.is_(None)
                    | (models.ModerationOutbox.last_attempt_at < cutoff)
                ),
            )
            .order_by(models.ModerationOutbox.created_at)
            .limit(_MAX_PER_RUN)
        )
    ).all()

    enqueued = 0
    for outbox, photo in rows:
        object_name = photo.canonical_object_name or photo.object_name
        result = await enqueue_moderation_work(
            observation_id=outbox.observation_id,
            photo_id=outbox.photo_id,
            bucket=photo.bucket,
            object_name=object_name,
            settings=settings,
        )
        outbox.last_attempt_at = datetime.now(UTC)
        if result.success:
            outbox.status = "enqueued"
            outbox.last_error = None
            enqueued += 1
        else:
            outbox.retry_count += 1
            outbox.last_error = result.reason or "unknown"
            outbox.status = "dlq" if outbox.retry_count >= _MAX_RETRIES else "failed"
        await session.commit()

    return enqueued


async def main() -> None:
    settings = get_settings()
    engine = create_async_engine(settings.sqlalchemy_database_url)
    sessions: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine,
        expire_on_commit=False,
    )
    try:
        async with sessions() as session:
            count = await relay(session, settings)
        print(f"moderation_outbox_relay: {count} row(s) enqueued")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
