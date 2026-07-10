"""Teacher / parent review queue.

`GET /v1/review-queue`              -> pending items for caller's groups
`POST /v1/review-queue/{id}/approve` -> move quarantine -> observations
`POST /v1/review-queue/{id}/reject`  -> tombstone and queue deterministic rebuild

Only adult roles (parent / teacher) can list or resolve review items.
The caller must be a member (with adult role) of the item's group --
checked via the `memberships` join.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Annotated, Literal

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import desc, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import CurrentUser, CurrentUserDep, resolve_current_user_row
from app.core.config import Settings, get_request_settings
from app.core.storage import SignedUrlGeneratorDep
from app.db import models
from app.db.session import DbSessionDep
from app.inat.enqueue import enqueue_inat_submit
from app.moderation.review_service import ReviewResolutionConflict, reject_review_item

router = APIRouter(prefix="/v1/review-queue", tags=["review_queue"])

log = structlog.get_logger()

_ADULT_ROLES: frozenset[str] = frozenset({"parent", "teacher"})

_DEFAULT_LIMIT = 20
_MAX_LIMIT = 50


# ---------------------------------------------------------------------------
# DTOs
# ---------------------------------------------------------------------------


class ReviewQueueItemResponse(BaseModel):
    id: str
    group_id: str
    photo_id: str
    observation_id: str | None
    status: str
    reason: str | None
    created_at: datetime


class ReviewQueueListResponse(BaseModel):
    items: list[ReviewQueueItemResponse]
    next_cursor: str | None = None


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


async def _resolve_adult_user(session: AsyncSession, current_user: CurrentUser) -> models.User:
    user = await resolve_current_user_row(session, current_user)
    if user.role not in _ADULT_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Review queue is parent/teacher only",
        )
    return user


async def _adult_groups(session: AsyncSession, user_id: str) -> list[str]:
    rows = (
        await session.execute(
            select(models.Membership.group_id).where(
                models.Membership.user_id == user_id,
                models.Membership.role.in_(_ADULT_ROLES),
            )
        )
    ).all()
    return [r[0] for r in rows]


# ---------------------------------------------------------------------------
# GET /v1/review-queue
# ---------------------------------------------------------------------------


@router.get("", response_model=ReviewQueueListResponse)
async def list_pending(
    current_user: CurrentUserDep,
    session: DbSessionDep,
    limit: Annotated[int, Query(ge=1, le=_MAX_LIMIT)] = _DEFAULT_LIMIT,
    before: Annotated[str | None, Query(min_length=26, max_length=26)] = None,
    review_status: Annotated[
        Literal["pending", "approved", "rejected"], Query(alias="status")
    ] = "pending",
) -> ReviewQueueListResponse:
    user = await _resolve_adult_user(session, current_user)
    group_ids = await _adult_groups(session, user.id)
    if not group_ids:
        return ReviewQueueListResponse(items=[], next_cursor=None)

    stmt = select(models.ReviewQueueItem).where(
        models.ReviewQueueItem.group_id.in_(group_ids),
        models.ReviewQueueItem.status == review_status,
    )
    if before is not None:
        stmt = stmt.where(models.ReviewQueueItem.id < before)
    stmt = stmt.order_by(desc(models.ReviewQueueItem.id)).limit(limit + 1)

    rows = (await session.execute(stmt)).scalars().all()
    has_more = len(rows) > limit
    page = rows[:limit]

    items = [
        ReviewQueueItemResponse(
            id=r.id,
            group_id=r.group_id,
            photo_id=r.photo_id,
            observation_id=r.observation_id,
            status=r.status,
            reason=r.reason,
            created_at=r.created_at,
        )
        for r in page
    ]

    return ReviewQueueListResponse(
        items=items,
        next_cursor=items[-1].id if has_more and items else None,
    )


# ---------------------------------------------------------------------------
# Resolve helpers (approve / reject share most of the same shape)
# ---------------------------------------------------------------------------


async def _load_review_for_resolution(
    session: AsyncSession,
    user: models.User,
    review_id: str,
    *,
    lock: bool = True,
) -> models.ReviewQueueItem:
    statement = select(models.ReviewQueueItem).where(models.ReviewQueueItem.id == review_id)
    if lock:
        statement = statement.with_for_update()
    review = (await session.execute(statement)).scalar_one_or_none()
    if review is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Review item not found")

    membership = (
        await session.execute(
            select(models.Membership.id).where(
                models.Membership.user_id == user.id,
                models.Membership.group_id == review.group_id,
                models.Membership.role.in_(_ADULT_ROLES),
            )
        )
    ).scalar_one_or_none()
    if membership is None:
        # Not in this group as an adult -> 404 like missing (no enumeration).
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Review item not found")

    if review.status != "pending":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Review item is already {review.status}",
        )

    return review


# ---------------------------------------------------------------------------
# POST /v1/review-queue/{id}/approve
# ---------------------------------------------------------------------------


class ResolveResponse(BaseModel):
    id: str
    status: str
    photo_status: str | None = Field(default=None)
    rebuild_id: str | None = None
    rebuild_status: str | None = None


@router.post("/{review_id}/approve", response_model=ResolveResponse)
async def approve_review(
    review_id: str,
    current_user: CurrentUserDep,
    session: DbSessionDep,
    storage: SignedUrlGeneratorDep,
    settings: Annotated[Settings, Depends(get_request_settings)],
) -> ResolveResponse:
    user = await _resolve_adult_user(session, current_user)
    review = await _load_review_for_resolution(session, user, review_id)

    photo = (
        await session.execute(select(models.Photo).where(models.Photo.id == review.photo_id))
    ).scalar_one_or_none()
    if photo is None:
        # Inconsistent state; treat as gone.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Photo gone")

    # Write and verify the destination first. The quarantined source remains
    # authoritative until the database commit succeeds.
    source_object = photo.object_name
    new_object = f"observations/{photo.id}.jpg"
    await asyncio.to_thread(
        storage.copy_object,
        src_bucket=photo.bucket,
        src_object=source_object,
        dst_bucket=photo.bucket,
        dst_object=new_object,
        expected_size=photo.byte_count,
        expected_sha256=photo.sha256,
    )

    photo.object_name = new_object
    photo.canonical_object_name = new_object
    photo.status = "clean"
    photo.moderated_at = datetime.now(UTC)

    review.status = "approved"
    review.reviewer_user_id = user.id
    review.resolved_at = datetime.now(UTC)

    # Risk 0002 transactional outbox: when this approval has a linked
    # observation, flip its moderation_status to `clean` and insert an
    # `inat_submit_outbox` row in the SAME commit. Post-commit we
    # attempt the Service Bus enqueue; on failure the row stays
    # `pending` for the 15-min replay job to retry.
    #
    # Gated on the Option B `inat_submit_enabled` flag (default False).
    # When False the moderation_status flip happens but the outbox row
    # is skipped -- the kid's observation never leaves Hinterland.
    observation_id_for_enqueue: str | None = None
    if review.observation_id is not None:
        observation = (
            await session.execute(
                select(models.Observation).where(models.Observation.id == review.observation_id)
            )
        ).scalar_one_or_none()
        if observation is not None:
            observation.moderation_status = "clean"
            observation.moderation_source = "adult"
            observation.moderation_policy_version = "adult-review-v1"
            if settings.inat_submit_enabled:
                session.add(
                    models.InatSubmitOutbox(
                        observation_id=observation.id,
                        status="pending",
                    )
                )
                observation_id_for_enqueue = observation.id

    await session.commit()

    try:
        await asyncio.to_thread(
            storage.delete_object,
            bucket=photo.bucket,
            object_name=source_object,
        )
    except Exception:
        log.warning(
            "review_queue.approve_source_cleanup_failed",
            review_id=review_id,
            photo_id=photo.id,
        )

    if observation_id_for_enqueue is not None:
        enq = await enqueue_inat_submit(observation_id_for_enqueue, settings=settings)
        now = datetime.now(UTC)
        if enq.success:
            await session.execute(
                update(models.InatSubmitOutbox)
                .where(models.InatSubmitOutbox.observation_id == observation_id_for_enqueue)
                .values(status="enqueued", last_attempt_at=now)
            )
        else:
            await session.execute(
                update(models.InatSubmitOutbox)
                .where(models.InatSubmitOutbox.observation_id == observation_id_for_enqueue)
                .values(
                    last_attempt_at=now,
                    retry_count=models.InatSubmitOutbox.retry_count + 1,
                    last_error=enq.reason or "unknown",
                )
            )
        await session.commit()

    log.info(
        "review_queue.approved",
        review_id=review_id,
        photo_id=photo.id,
        reviewer=user.id,
        outbox_observation=observation_id_for_enqueue,
    )
    return ResolveResponse(id=review.id, status="approved", photo_status="clean")


# ---------------------------------------------------------------------------
# POST /v1/review-queue/{id}/reject
# ---------------------------------------------------------------------------


@router.post("/{review_id}/reject", response_model=ResolveResponse)
async def reject_review(
    review_id: str,
    current_user: CurrentUserDep,
    session: DbSessionDep,
    settings: Annotated[Settings, Depends(get_request_settings)],
) -> ResolveResponse:
    user = await _resolve_adult_user(session, current_user)
    # Authorization is checked without a row lock. The shared rejection
    # service then takes user-advisory -> review -> observation locks.
    review = await _load_review_for_resolution(session, user, review_id, lock=False)

    try:
        rebuild = await reject_review_item(
            session,
            review=review,
            reviewer_user_id=user.id,
        )
    except ReviewResolutionConflict as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc

    await session.commit()

    log.info(
        "review_queue.rejected",
        review_id=review_id,
        photo_id=review.photo_id,
        reviewer=user.id,
    )
    return ResolveResponse(
        id=review.id,
        status="rejected",
        photo_status="deleted",
        rebuild_id=rebuild.id if rebuild is not None else None,
        rebuild_status=rebuild.status if rebuild is not None else None,
    )


class RebuildStatusItem(BaseModel):
    id: str
    user_id: str
    trigger_observation_id: str | None
    status: str
    attempt_count: int
    last_error: str | None
    created_at: datetime
    finished_at: datetime | None


class RebuildStatusResponse(BaseModel):
    items: list[RebuildStatusItem]


@router.get("/rebuilds", response_model=RebuildStatusResponse)
async def list_rebuilds(
    current_user: CurrentUserDep,
    session: DbSessionDep,
    group_id: Annotated[str | None, Query(min_length=26, max_length=26)] = None,
) -> RebuildStatusResponse:
    """Adult-only status for rebuilds affecting one of the caller's groups."""
    user = await _resolve_adult_user(session, current_user)
    group_ids = await _adult_groups(session, user.id)
    if group_id is not None:
        if group_id not in group_ids:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
        group_ids = [group_id]
    if not group_ids:
        return RebuildStatusResponse(items=[])

    rows = (
        (
            await session.execute(
                select(models.DerivedStateRebuild)
                .join(
                    models.Membership,
                    models.Membership.user_id == models.DerivedStateRebuild.user_id,
                )
                .where(models.Membership.group_id.in_(group_ids))
                .distinct()
                .order_by(desc(models.DerivedStateRebuild.created_at))
                .limit(100)
            )
        )
        .scalars()
        .all()
    )
    return RebuildStatusResponse(
        items=[
            RebuildStatusItem(
                id=row.id,
                user_id=row.user_id,
                trigger_observation_id=row.trigger_observation_id,
                status=row.status,
                attempt_count=row.attempt_count,
                last_error=row.last_error,
                created_at=row.created_at,
                finished_at=row.finished_at,
            )
            for row in rows
        ]
    )
