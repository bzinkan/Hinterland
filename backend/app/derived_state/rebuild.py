"""Deterministic compensation for rejection and identification changes.

The observation row is authoritative. Everything rebuilt here is a materialized
view of non-rejected observations. One PostgreSQL transaction and a per-user
advisory lock ensure readers see either the prior consistent snapshot or the
replacement snapshot, never a half-rebuilt leaderboard/Dex/Sanctuary.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import delete, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID

from app.db import models
from app.dispatcher.core import dispatch
from app.dispatcher.registry import HANDLERS
from app.dispatcher.types import Context

log = structlog.get_logger()

_MAX_ATTEMPTS = 5
_RUNNING_LEASE = timedelta(minutes=15)


class RebuildIncomplete(RuntimeError):
    """At least one accepted observation could not be fully dispatched."""


async def acquire_user_lock(session: AsyncSession, user_id: str) -> None:
    """Serialize submission, correction, and rebuild work for one user."""
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:user_id, 0))"),
        {"user_id": user_id},
    )


async def try_acquire_user_lock(session: AsyncSession, user_id: str) -> bool:
    """Attempt the same transaction lock without waiting for another worker."""
    acquired = await session.scalar(
        text("SELECT pg_try_advisory_xact_lock(hashtextextended(:user_id, 0))"),
        {"user_id": user_id},
    )
    return bool(acquired)


async def enqueue_rebuild(
    session: AsyncSession,
    *,
    user_id: str,
    trigger_observation_id: str | None,
) -> models.DerivedStateRebuild:
    """Return the active job or create one in the caller's transaction."""
    await acquire_user_lock(session, user_id)
    existing = (
        await session.execute(
            select(models.DerivedStateRebuild)
            .where(
                models.DerivedStateRebuild.user_id == user_id,
                models.DerivedStateRebuild.status.in_(("queued", "running")),
            )
            .order_by(models.DerivedStateRebuild.created_at)
            .limit(1)
        )
    ).scalar_one_or_none()
    if existing is not None:
        # Preserve the newest trigger for operator context. The rebuild always
        # reads the complete current authoritative history.
        if trigger_observation_id is not None:
            existing.trigger_observation_id = trigger_observation_id
        return existing

    job = models.DerivedStateRebuild(
        id=str(ULID()),
        user_id=user_id,
        trigger_observation_id=trigger_observation_id,
        status="queued",
        attempt_count=0,
    )
    session.add(job)
    await session.flush()
    return job


async def rebuild_user_state(session: AsyncSession, *, user_id: str) -> None:
    """Replace every observation-derived projection for ``user_id``.

    The caller owns the transaction. Any partial handler result raises so the
    caller rolls the whole replacement back and retries the job.
    """
    await acquire_user_lock(session, user_id)

    user = (
        await session.execute(select(models.User).where(models.User.id == user_id))
    ).scalar_one_or_none()
    if user is None:
        raise RebuildIncomplete(f"user {user_id!r} no longer exists")

    observation_ids = select(models.Observation.id).where(models.Observation.user_id == user_id)
    await session.execute(
        update(models.Observation)
        .where(models.Observation.user_id == user_id)
        .values(rewards=[], dispatch_status="unverified", dispatched_at=None)
    )
    await session.execute(
        delete(models.ObservationHandlerRun).where(
            models.ObservationHandlerRun.observation_id.in_(observation_ids)
        )
    )
    await session.execute(
        delete(models.ExpeditionObservationContribution).where(
            models.ExpeditionObservationContribution.observation_id.in_(observation_ids)
        )
    )
    await session.execute(delete(models.DexEntry).where(models.DexEntry.user_id == user_id))
    await session.execute(
        delete(models.SanctuaryEvent).where(models.SanctuaryEvent.user_id == user_id)
    )
    await session.execute(
        delete(models.SanctuaryObservationContribution).where(
            models.SanctuaryObservationContribution.user_id == user_id
        )
    )
    await session.execute(
        delete(models.SanctuaryElement).where(models.SanctuaryElement.user_id == user_id)
    )
    await session.execute(
        delete(models.SanctuaryZoneState).where(models.SanctuaryZoneState.user_id == user_id)
    )

    progress_rows = (
        (
            await session.execute(
                select(models.ExpeditionProgress).where(
                    models.ExpeditionProgress.user_id == user_id
                )
            )
        )
        .scalars()
        .all()
    )
    for progress in progress_rows:
        progress.completed_steps = {}
        progress.completed_at = None

    memberships = (
        (
            await session.execute(
                select(models.Membership).where(models.Membership.user_id == user_id)
            )
        )
        .scalars()
        .all()
    )
    membership_by_group = {membership.group_id: membership for membership in memberships}
    for membership in memberships:
        membership.observation_count = 0
        membership.dex_count = 0
        membership.rarest_tier = None
        membership.last_observed_at = None

    rows = (
        await session.execute(
            select(models.Observation, models.Photo, models.Group)
            .join(models.Photo, models.Observation.photo_id == models.Photo.id)
            .join(models.Group, models.Observation.group_id == models.Group.id)
            .where(
                models.Observation.user_id == user_id,
                models.Observation.rejected_at.is_(None),
                models.Observation.moderation_status != "rejected",
            )
            .order_by(models.Observation.observed_at, models.Observation.id)
        )
    ).all()

    counts: dict[str, int] = defaultdict(int)
    last_seen: dict[str, datetime] = {}
    for observation, _photo, _group in rows:
        counts[observation.group_id] += 1
        observed_at = observation.observed_at or observation.created_at
        previous = last_seen.get(observation.group_id)
        if previous is None or observed_at > previous:
            last_seen[observation.group_id] = observed_at
        observation.rewards = []
        observation.dispatch_status = "pending"
        observation.dispatched_at = None

    for group_id, count in counts.items():
        current_membership = membership_by_group.get(group_id)
        if current_membership is None:
            raise RebuildIncomplete(
                f"accepted observations exist without membership for group {group_id!r}"
            )
        current_membership.observation_count = count
        current_membership.last_observed_at = last_seen[group_id]

    await session.flush()

    for observation, photo, group in rows:
        await dispatch(
            Context(
                db=session,
                user=user,
                group=group,
                observation=observation,
                photo=photo,
            ),
            HANDLERS,
            commit=False,
        )
        if observation.dispatch_status != "complete":
            raise RebuildIncomplete(
                f"observation {observation.id} rebuilt as {observation.dispatch_status}"
            )

    log.info(
        "derived_state.rebuild.complete",
        user_id=user_id,
        observation_count=len(rows),
    )


async def process_rebuild_job(session: AsyncSession, *, job_id: str) -> bool:
    """Claim and execute one job. Returns True only on full success."""
    stale_before = datetime.now(UTC) - _RUNNING_LEASE
    # Resolve ownership without a row lock. Every derived-state writer uses
    # user-advisory -> row-lock ordering, so claiming the job row first would
    # deadlock a correction that already holds the user lock and is updating
    # this active job's trigger.
    user_id = await session.scalar(
        select(models.DerivedStateRebuild.user_id).where(
            models.DerivedStateRebuild.id == job_id,
            or_(
                models.DerivedStateRebuild.status.in_(("queued", "failed")),
                (
                    (models.DerivedStateRebuild.status == "running")
                    & (models.DerivedStateRebuild.started_at < stale_before)
                ),
            ),
            models.DerivedStateRebuild.attempt_count < _MAX_ATTEMPTS,
        )
    )
    if user_id is None:
        await session.rollback()
        return False

    await acquire_user_lock(session, user_id)
    job = (
        await session.execute(
            select(models.DerivedStateRebuild)
            .where(
                models.DerivedStateRebuild.id == job_id,
                or_(
                    models.DerivedStateRebuild.status.in_(("queued", "failed")),
                    (
                        (models.DerivedStateRebuild.status == "running")
                        & (models.DerivedStateRebuild.started_at < stale_before)
                    ),
                ),
                models.DerivedStateRebuild.attempt_count < _MAX_ATTEMPTS,
            )
            .with_for_update(skip_locked=True)
        )
    ).scalar_one_or_none()
    if job is None:
        await session.rollback()
        return False

    job.status = "running"
    job.attempt_count += 1
    job.started_at = datetime.now(UTC)
    job.finished_at = None
    job.last_error = None
    attempt = job.attempt_count
    await session.commit()

    try:
        async with session.begin():
            await acquire_user_lock(session, user_id)
            running_job = (
                await session.execute(
                    select(models.DerivedStateRebuild)
                    .where(
                        models.DerivedStateRebuild.id == job_id,
                        models.DerivedStateRebuild.user_id == user_id,
                        models.DerivedStateRebuild.status == "running",
                        models.DerivedStateRebuild.attempt_count == attempt,
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if running_job is None:
                raise RebuildIncomplete(f"rebuild job {job_id} lost its running claim")
            await rebuild_user_state(session, user_id=user_id)
            running_job.status = "succeeded"
            running_job.finished_at = datetime.now(UTC)
    except Exception as exc:  # job boundary must record failure
        await session.rollback()
        async with session.begin():
            await acquire_user_lock(session, user_id)
            failed_job = (
                await session.execute(
                    select(models.DerivedStateRebuild)
                    .where(
                        models.DerivedStateRebuild.id == job_id,
                        models.DerivedStateRebuild.user_id == user_id,
                        models.DerivedStateRebuild.status == "running",
                        models.DerivedStateRebuild.attempt_count == attempt,
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if failed_job is not None:
                failed_job.status = "queued" if attempt < _MAX_ATTEMPTS else "failed"
                failed_job.last_error = f"{type(exc).__name__}: {exc}"[:4000]
                failed_job.finished_at = datetime.now(UTC)
        log.exception(
            "derived_state.rebuild.failed",
            rebuild_id=job_id,
            user_id=user_id,
            attempt=attempt,
        )
        return False

    return True
