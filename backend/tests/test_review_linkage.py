"""Fail-closed tests for review/photo/observation authority linkage."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes.review_queue import _load_review_for_resolution
from app.db import models
from app.moderation.review_service import (
    ReviewResolutionConflict,
    lock_linked_review_subject,
)
from app.moderation.revocation import revoke_and_reject_review_item

_PARENT_ID = "01J0PARENTID000000000ULID"
_KID_ID = "01J0KIDID0000000000000ULID"
_GROUP_ID = "01J0GROUPID00000000000ULID"
_PHOTO_ID = "01J0PHOTOID00000000000ULID"
_OBS_ID = "01J0OBSID00000000000000ULID"
_REVIEW_ID = "01J0REVIEWID0000000000ULID"


def _result(value: object) -> MagicMock:
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


def _parent() -> models.User:
    return models.User(id=_PARENT_ID, firebase_uid="parent", role="parent")


def _review(*, status: str = "pending") -> models.ReviewQueueItem:
    return models.ReviewQueueItem(
        id=_REVIEW_ID,
        group_id=_GROUP_ID,
        photo_id=_PHOTO_ID,
        observation_id=_OBS_ID,
        status=status,
    )


def _photo(*, clean: bool = False) -> models.Photo:
    return models.Photo(
        id=_PHOTO_ID,
        user_id=_KID_ID,
        bucket="private",
        object_name=(f"observations/{_PHOTO_ID}.jpg" if clean else f"quarantine/{_PHOTO_ID}.jpg"),
        canonical_object_name=(
            f"observations/{_PHOTO_ID}.jpg" if clean else f"quarantine/{_PHOTO_ID}.jpg"
        ),
        status="clean" if clean else "quarantine",
        attachment_status="attached",
        byte_count=4,
        sha256="0" * 64,
    )


def _observation(
    *,
    observation_id: str = _OBS_ID,
    photo_id: str = _PHOTO_ID,
    user_id: str = _KID_ID,
    group_id: str = _GROUP_ID,
    clean: bool = False,
) -> models.Observation:
    return models.Observation(
        id=observation_id,
        user_id=user_id,
        group_id=group_id,
        photo_id=photo_id,
        moderation_status="clean" if clean else "quarantine",
    )


class _NoTouchStorage:
    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"storage must not be touched for an inconsistent review: {name}")


@pytest.mark.parametrize(
    ("mismatch", "observation"),
    [
        ("review-observation", _observation(observation_id="01J0OTHEROBS0000000000000")),
        ("observation-photo", _observation(photo_id="01J0OTHERPHOTO00000000000")),
        ("observation-user", _observation(user_id="01J0OTHERKID0000000000000")),
        ("observation-group", _observation(group_id="01J0OTHERGROUP00000000000")),
    ],
)
@pytest.mark.parametrize("claim_status", ["pending", "approved"])
async def test_reject_and_revoke_stop_before_storage_on_linkage_mismatch(
    mismatch: str,
    observation: models.Observation,
    claim_status: str,
) -> None:
    del mismatch
    review = _review(status=claim_status)
    photo = _photo(clean=claim_status == "approved")
    session = AsyncMock(spec=AsyncSession)
    session.execute = AsyncMock(side_effect=[_result(photo), _result(observation)])

    with pytest.raises(ReviewResolutionConflict, match="linkage is inconsistent"):
        await revoke_and_reject_review_item(
            session,
            storage=_NoTouchStorage(),  # type: ignore[arg-type]
            review=review,
            reviewer_user_id=_PARENT_ID,
            source="test",
            claim_review_status=claim_status,
        )

    assert session.execute.await_count == 2
    session.commit.assert_not_awaited()
    session.flush.assert_not_awaited()
    assert review.status == claim_status
    assert photo.status == ("clean" if claim_status == "approved" else "quarantine")


async def test_approval_authority_query_requires_the_whole_linked_tuple() -> None:
    """An inconsistent tuple is indistinguishable from a missing review."""
    review = _review()
    session = AsyncMock(spec=AsyncSession)
    session.execute = AsyncMock(side_effect=[_result(review), _result(None)])

    with pytest.raises(HTTPException) as exc_info:
        await _load_review_for_resolution(session, _parent(), _REVIEW_ID)

    assert exc_info.value.status_code == 404
    authority = session.execute.await_args_list[1].args[0]
    sql = str(
        authority.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    assert "photos.id = observations.photo_id" in sql
    assert "observations.user_id = photos.user_id" in sql
    assert f"observations.group_id = '{_GROUP_ID}'" in sql
    assert f"observations.id = '{_OBS_ID}'" in sql
    assert f"users.parent_user_id = '{_PARENT_ID}'" in sql


async def test_approval_rechecks_linkage_after_advisory_and_review_locks() -> None:
    candidate = _review()
    locked_review = _review()
    locked_review.group_id = "01J0OTHERGROUP00000000000"
    photo = _photo()
    observation = _observation()
    session = AsyncMock(spec=AsyncSession)
    session.execute = AsyncMock(
        side_effect=[
            _result(candidate),
            _result(_OBS_ID),
            _result(photo),
            _result(observation),
            MagicMock(),  # child advisory lock
            _result(locked_review),
            _result(photo),
            _result(observation),
        ]
    )

    with pytest.raises(HTTPException) as exc_info:
        await _load_review_for_resolution(session, _parent(), _REVIEW_ID)

    assert exc_info.value.status_code == 409
    assert "linkage is inconsistent" in str(exc_info.value.detail)
    statements = [str(call.args[0]) for call in session.execute.await_args_list]
    advisory_index = next(i for i, statement in enumerate(statements) if "pg_advisory" in statement)
    review_lock_index = next(
        i
        for i, statement in enumerate(statements)
        if "review_queue" in statement and "FOR UPDATE" in statement
    )
    assert advisory_index < review_lock_index


async def test_locked_resolution_rechecks_canonical_parent() -> None:
    review = _review()
    photo = _photo()
    observation = _observation()
    session = AsyncMock(spec=AsyncSession)
    session.execute = AsyncMock(
        side_effect=[
            _result(photo),
            _result(observation),
            MagicMock(),  # child advisory lock
            _result(review),
            _result(photo),
            _result(observation),
            _result(None),  # canonical relationship changed or never existed
        ]
    )

    with pytest.raises(ReviewResolutionConflict, match="not managed by this parent"):
        await lock_linked_review_subject(
            session,
            review=review,
            expected_status="pending",
            canonical_parent_user_id=_PARENT_ID,
        )

    assert review.status == "pending"
    assert photo.status == "quarantine"


async def test_locked_resolution_rejects_subject_change_after_advisory_lock() -> None:
    review = _review()
    candidate_photo = _photo()
    candidate_observation = _observation()
    changed_user_id = "01J0CHANGEDKID000000000000"
    locked_photo = _photo()
    locked_photo.user_id = changed_user_id
    locked_observation = _observation(user_id=changed_user_id)
    session = AsyncMock(spec=AsyncSession)
    session.execute = AsyncMock(
        side_effect=[
            _result(candidate_photo),
            _result(candidate_observation),
            MagicMock(),  # advisory lock for the original child
            _result(review),
            _result(locked_photo),
            _result(locked_observation),
        ]
    )

    with pytest.raises(ReviewResolutionConflict, match="changed while being resolved"):
        await lock_linked_review_subject(
            session,
            review=review,
            expected_status="pending",
        )

    assert review.status == "pending"
    assert locked_photo.status == "quarantine"
