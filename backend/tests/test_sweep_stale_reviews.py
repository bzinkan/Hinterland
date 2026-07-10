"""Tests for admin/sweep_stale_reviews.py."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from admin.sweep_stale_reviews import sweep
from app.db import models


def _review(
    review_id: str = "01J0REVIEWID0000000000ULID", status: str = "pending"
) -> models.ReviewQueueItem:
    from datetime import UTC, datetime, timedelta

    r = models.ReviewQueueItem(
        id=review_id,
        group_id="g1",
        photo_id="p1",
        observation_id="o1",
        status=status,
        reason='{"adult":"LIKELY"}',
    )
    # Server defaults don't fire on in-memory construction; the sweep
    # logs `now - created_at` so we need a real timestamp.
    r.created_at = datetime.now(UTC) - timedelta(days=45)
    return r


def _photo() -> models.Photo:
    return models.Photo(
        id="p1",
        user_id="u1",
        bucket="b",
        object_name="quarantine/p1.jpg",
        status="quarantine",
        content_type="image/jpeg",
    )


def _obs() -> models.Observation:
    return models.Observation(
        id="o1",
        user_id="u1",
        group_id="g1",
        photo_id="p1",
        latitude=39.1,
        longitude=-84.5,
    )


def _wire(
    fake_session: AsyncMock,
    *,
    rows: list[tuple[models.ReviewQueueItem, models.Photo, models.Observation | None]],
) -> None:
    list_result = MagicMock()
    scalars_result = MagicMock()
    scalars_result.all = MagicMock(return_value=[review for review, _photo, _obs in rows])
    list_result.scalars = MagicMock(return_value=scalars_result)
    side_effects: list[Any] = [list_result]
    for review_row, photo, observation in rows:
        subject_observation_result = MagicMock()
        subject_observation_result.scalar_one_or_none = MagicMock(
            return_value=observation.user_id if observation is not None else None
        )
        subject_photo_result = MagicMock()
        subject_photo_result.scalar_one_or_none = MagicMock(return_value=photo.user_id)
        locked_review_result = MagicMock()
        locked_review_result.scalar_one_or_none = MagicMock(return_value=review_row)
        photo_result = MagicMock()
        photo_result.scalar_one_or_none = MagicMock(return_value=photo)
        observation_result = MagicMock()
        observation_result.scalar_one_or_none = MagicMock(return_value=observation)
        side_effects.append(subject_observation_result)
        if observation is None:
            side_effects.append(subject_photo_result)
        side_effects.extend([locked_review_result, photo_result, observation_result])
        if observation is not None:
            rebuild_lock_result = MagicMock()
            rebuild_result = MagicMock()
            rebuild_result.scalar_one_or_none = MagicMock(return_value=None)
            side_effects.extend([rebuild_lock_result, rebuild_result])
    fake_session.execute = AsyncMock(side_effect=side_effects)
    fake_session.scalar = AsyncMock(return_value=True)
    fake_session.commit = AsyncMock()
    fake_session.flush = AsyncMock()
    fake_session.add = MagicMock()


@pytest.fixture
def fake_session() -> AsyncMock:
    return AsyncMock(spec=AsyncSession)


async def test_sweep_no_stale_rows_returns_zero(fake_session: AsyncMock) -> None:
    _wire(fake_session, rows=[])
    count = await sweep(fake_session)
    assert count == 0
    fake_session.commit.assert_not_called()


async def test_sweep_auto_rejects_each_stale_row(fake_session: AsyncMock) -> None:
    review = _review()
    photo = _photo()
    obs = _obs()
    _wire(fake_session, rows=[(review, photo, obs)])

    count = await sweep(fake_session)
    assert count == 1

    assert review.status == "rejected"
    assert review.reviewer_user_id is None  # auto, no human
    assert review.resolved_at is not None
    assert photo.status == "deleted"
    assert photo.moderated_at is not None
    fake_session.commit.assert_awaited_once()


async def test_sweep_still_rejects_when_observation_already_gone(
    fake_session: AsyncMock,
) -> None:
    review = _review()
    photo = _photo()
    _wire(fake_session, rows=[(review, photo, None)])

    count = await sweep(fake_session)
    assert count == 1
    assert review.status == "rejected"
    assert photo.status == "deleted"
    # Candidate scan; subject observation/photo lookup; locked review/photo/
    # observation. The non-blocking advisory attempt uses session.scalar.
    assert fake_session.execute.await_count == 6
    fake_session.scalar.assert_awaited_once()
    fake_session.commit.assert_awaited_once()


async def test_sweep_processes_multiple_in_one_pass(fake_session: AsyncMock) -> None:
    rows = [
        (_review("r1"), _photo(), _obs()),
        (_review("r2"), _photo(), _obs()),
        (_review("r3"), _photo(), _obs()),
    ]
    _wire(fake_session, rows=rows)
    count = await sweep(fake_session)
    assert count == 3
    assert fake_session.commit.await_count == 3
    assert all(r[0].status == "rejected" for r in rows)
