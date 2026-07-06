"""Teacher / parent review queue.

`GET /v1/review-queue`              -> pending items for caller's groups
`POST /v1/review-queue/{id}/approve` -> move quarantine -> observations
`POST /v1/review-queue/{id}/reject`  -> mark rejected, decrement counter

Only adult roles (parent / teacher) can list or resolve review items.
The caller must be a member (with adult role) of the item's group --
checked via the `memberships` join.
"""

from __future__ import annotations

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
) -> models.ReviewQueueItem:
    review = (
        await session.execute(
            select(models.ReviewQueueItem).where(models.ReviewQueueItem.id == review_id)
        )
    ).scalar_one_or_none()
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

    # Move quarantine/<id>.jpg back to observations/<id>.jpg.
    new_object = f"observations/{photo.id}.jpg"
    storage.copy_object(
        src_bucket=photo.bucket,
        src_object=photo.object_name,
        dst_bucket=photo.bucket,
        dst_object=new_object,
    )
    storage.delete_object(bucket=photo.bucket, object_name=photo.object_name)

    photo.object_name = new_object
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
            if settings.inat_submit_enabled:
                session.add(
                    models.InatSubmitOutbox(
                        observation_id=observation.id,
                        status="pending",
                    )
                )
                observation_id_for_enqueue = observation.id

    await session.commit()

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
    review = await _load_review_for_resolution(session, user, review_id)

    photo = (
        await session.execute(select(models.Photo).where(models.Photo.id == review.photo_id))
    ).scalar_one_or_none()
    if photo is not None:
        photo.status = "deleted"
        photo.moderated_at = datetime.now(UTC)
        # Photo bytes stay in quarantine/ for the 90d lifecycle rule (see
        # docs/moderation.md "Quarantine moves, it doesn't delete").

    # Decrement the kid's observation_count if we know which observation
    # this is. Per docs/moderation.md the counter MUST come back down on
    # reject (it was bumped at submission, before moderation ran).
    if review.observation_id is not None:
        observation = (
            await session.execute(
                select(models.Observation).where(models.Observation.id == review.observation_id)
            )
        ).scalar_one_or_none()
        if observation is not None:
            # Mark the observation rejected so the iNat submit consumer
            # never picks it up even if a stale outbox row exists.
            observation.moderation_status = "rejected"
            await session.execute(
                update(models.Membership)
                .where(
                    models.Membership.user_id == observation.user_id,
                    models.Membership.group_id == observation.group_id,
                )
                .values(observation_count=models.Membership.observation_count - 1)
            )

    review.status = "rejected"
    review.reviewer_user_id = user.id
    review.resolved_at = datetime.now(UTC)

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
        photo_status=photo.status if photo is not None else None,
    )
