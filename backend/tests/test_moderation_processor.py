"""Unit tests for the moderation processor.

Stubs out the storage facade and the Moderator; verifies the GCS
move + DB updates + review_queue insertion logic.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import models
from app.moderation.processor import PhotoNotFound, process_pending_photo
from app.moderation.provider import ModerationResult, ModerationUnavailable

_PHOTO_ID = "01J0PHOTOID00000000000ULID"
_OBS_ID = "01J0OBSID00000000000000ULID"
_GROUP_ID = "01J0GROUPID00000000000ULID"
_BUCKET = "dragonfly-photos-test"
_OBJECT_NAME = f"pending/{_PHOTO_ID}.jpg"


class _StubStorage:
    """Tracks copy_object + delete_object calls; serves canned bytes."""

    def __init__(self, bytes_to_return: bytes = b"jpeg") -> None:
        self.bytes_to_return = bytes_to_return
        self.copy_calls: list[tuple[str, str, str, str]] = []
        self.delete_calls: list[tuple[str, str]] = []

    def generate_put_url(self, **_: Any) -> tuple[str, Any]:
        raise NotImplementedError

    def fetch_object_bytes(self, *, bucket: str, object_name: str) -> bytes:
        return self.bytes_to_return

    def copy_object(
        self,
        *,
        src_bucket: str,
        src_object: str,
        dst_bucket: str,
        dst_object: str,
    ) -> None:
        self.copy_calls.append((src_bucket, src_object, dst_bucket, dst_object))

    def delete_object(self, *, bucket: str, object_name: str) -> None:
        self.delete_calls.append((bucket, object_name))

    def generate_get_url(self, **_: object) -> tuple[str, object]:
        raise NotImplementedError


class _StubModerator:
    def __init__(self, result: ModerationResult | Exception) -> None:
        self._result = result

    async def moderate(self, image_bytes: bytes) -> ModerationResult:
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


def _photo_row(status: str = "pending") -> models.Photo:
    return models.Photo(
        id=_PHOTO_ID,
        user_id="user-id",
        bucket=_BUCKET,
        object_name=_OBJECT_NAME,
        status=status,
        content_type="image/jpeg",
    )


def _obs_row() -> models.Observation:
    return models.Observation(
        id=_OBS_ID,
        user_id="user-id",
        group_id=_GROUP_ID,
        photo_id=_PHOTO_ID,
        latitude=39.1,
        longitude=-84.5,
    )


@pytest.fixture
def fake_session() -> AsyncMock:
    return AsyncMock(spec=AsyncSession)


def _wire_session(
    fake_session: AsyncMock,
    *,
    photo: models.Photo | None,
    observation: models.Observation | None = None,
) -> None:
    photo_result = MagicMock()
    photo_result.scalar_one_or_none = MagicMock(return_value=photo)

    obs_result = MagicMock()
    obs_result.scalar_one_or_none = MagicMock(return_value=observation)

    side_effects: list[Any] = [photo_result]
    # The processor only looks up the observation on the flagged path.
    side_effects.append(obs_result)

    fake_session.execute = AsyncMock(side_effect=side_effects)
    fake_session.add = MagicMock()
    fake_session.commit = AsyncMock()


# ---------------------------------------------------------------------------


async def test_skipped_when_object_not_in_pending(fake_session: AsyncMock) -> None:
    storage = _StubStorage()
    moderator = _StubModerator(ModerationResult(decision="clean"))

    result = await process_pending_photo(
        fake_session,
        storage,
        moderator,
        bucket=_BUCKET,
        object_name="observations/something.jpg",
    )
    assert result.decision == "skipped"
    assert storage.copy_calls == []
    assert storage.delete_calls == []


async def test_raises_when_photo_row_missing(fake_session: AsyncMock) -> None:
    _wire_session(fake_session, photo=None)
    storage = _StubStorage()
    moderator = _StubModerator(ModerationResult(decision="clean"))

    with pytest.raises(PhotoNotFound):
        await process_pending_photo(
            fake_session,
            storage,
            moderator,
            bucket=_BUCKET,
            object_name=_OBJECT_NAME,
        )


async def test_skipped_when_photo_already_moderated(fake_session: AsyncMock) -> None:
    _wire_session(fake_session, photo=_photo_row(status="clean"))
    storage = _StubStorage()
    moderator = _StubModerator(ModerationResult(decision="flagged"))

    result = await process_pending_photo(
        fake_session,
        storage,
        moderator,
        bucket=_BUCKET,
        object_name=_OBJECT_NAME,
    )
    assert result.decision == "skipped"
    assert storage.copy_calls == []


async def test_clean_path_moves_to_observations_and_updates_photo(
    fake_session: AsyncMock,
) -> None:
    photo = _photo_row()
    _wire_session(fake_session, photo=photo)
    storage = _StubStorage()
    moderator = _StubModerator(ModerationResult(decision="clean"))

    result = await process_pending_photo(
        fake_session,
        storage,
        moderator,
        bucket=_BUCKET,
        object_name=_OBJECT_NAME,
    )
    assert result.decision == "clean"
    assert result.new_object_name == f"observations/{_PHOTO_ID}.jpg"
    assert result.review_queue_id is None
    assert storage.copy_calls == [(_BUCKET, _OBJECT_NAME, _BUCKET, f"observations/{_PHOTO_ID}.jpg")]
    assert storage.delete_calls == [(_BUCKET, _OBJECT_NAME)]
    # Photo row mutated in place
    assert photo.status == "clean"
    assert photo.object_name == f"observations/{_PHOTO_ID}.jpg"
    assert photo.moderated_at is not None
    fake_session.commit.assert_awaited_once()


async def test_flagged_path_moves_to_quarantine_and_inserts_review_row(
    fake_session: AsyncMock,
) -> None:
    photo = _photo_row()
    obs = _obs_row()
    _wire_session(fake_session, photo=photo, observation=obs)
    storage = _StubStorage()
    moderator = _StubModerator(
        ModerationResult(
            decision="flagged",
            labels={"adult": "LIKELY", "violence": "POSSIBLE"},
        )
    )

    result = await process_pending_photo(
        fake_session,
        storage,
        moderator,
        bucket=_BUCKET,
        object_name=_OBJECT_NAME,
    )
    assert result.decision == "flagged"
    assert result.new_object_name == f"quarantine/{_PHOTO_ID}.jpg"
    assert result.review_queue_id is not None

    assert storage.copy_calls == [(_BUCKET, _OBJECT_NAME, _BUCKET, f"quarantine/{_PHOTO_ID}.jpg")]
    assert photo.status == "quarantine"
    assert photo.object_name == f"quarantine/{_PHOTO_ID}.jpg"

    # review_queue row was added
    fake_session.add.assert_called_once()
    review: models.ReviewQueueItem = fake_session.add.call_args.args[0]
    assert isinstance(review, models.ReviewQueueItem)
    assert review.group_id == _GROUP_ID
    assert review.photo_id == _PHOTO_ID
    assert review.observation_id == _OBS_ID
    assert review.status == "pending"
    assert review.reason is not None
    assert "adult" in review.reason


async def test_flagged_with_no_observation_skips_review_row(
    fake_session: AsyncMock,
) -> None:
    """Presign-then-no-create -> photo exists but observation doesn't.
    Quarantine the photo, leave it for the lifecycle rule, no review row."""
    _wire_session(fake_session, photo=_photo_row(), observation=None)
    storage = _StubStorage()
    moderator = _StubModerator(
        ModerationResult(decision="flagged", labels={"adult": "VERY_LIKELY"})
    )

    result = await process_pending_photo(
        fake_session,
        storage,
        moderator,
        bucket=_BUCKET,
        object_name=_OBJECT_NAME,
    )
    assert result.decision == "flagged"
    assert result.review_queue_id is None
    fake_session.add.assert_not_called()


# ---------------------------------------------------------------------------
# Risk 0002 transactional outbox tests
# ---------------------------------------------------------------------------


def _wire_session_with_outbox_update(
    fake_session: AsyncMock,
    *,
    photo: models.Photo | None,
    observation: models.Observation | None,
) -> None:
    """Like ``_wire_session`` but adds a 3rd side_effect for the outbox
    UPDATE that fires after the post-commit enqueue attempt."""
    photo_result = MagicMock()
    photo_result.scalar_one_or_none = MagicMock(return_value=photo)

    obs_result = MagicMock()
    obs_result.scalar_one_or_none = MagicMock(return_value=observation)

    update_result = MagicMock()  # session.execute(update(...)) returns

    fake_session.execute = AsyncMock(side_effect=[photo_result, obs_result, update_result])
    fake_session.add = MagicMock()
    fake_session.commit = AsyncMock()


async def test_clean_with_observation_and_no_settings_skips_outbox(
    fake_session: AsyncMock,
) -> None:
    """Option B default: when settings=None the processor flips
    moderation_status but does NOT write an outbox row. The kid's
    observation stays entirely inside Hinterland."""
    photo = _photo_row()
    obs = _obs_row()
    _wire_session(fake_session, photo=photo, observation=obs)
    storage = _StubStorage()
    moderator = _StubModerator(ModerationResult(decision="clean"))

    result = await process_pending_photo(
        fake_session,
        storage,
        moderator,
        bucket=_BUCKET,
        object_name=_OBJECT_NAME,
        settings=None,
    )
    assert result.decision == "clean"
    assert result.observation_id == _OBS_ID
    assert result.outbox_status is None
    # Observation flipped to clean in the SAME transaction.
    assert obs.moderation_status == "clean"
    # No outbox row added.
    fake_session.add.assert_not_called()


async def test_clean_with_observation_and_inat_disabled_default_skips_outbox(
    fake_session: AsyncMock,
) -> None:
    """Option B default: `inat_submit_enabled=False` (default) skips
    the outbox write even when Service Bus is configured. iNat-submit
    pipeline is dormant until the operator flips the flag for a Phase 3
    family-account model."""
    from app.core.config import Settings

    photo = _photo_row()
    obs = _obs_row()
    _wire_session(fake_session, photo=photo, observation=obs)
    storage = _StubStorage()
    moderator = _StubModerator(ModerationResult(decision="clean"))

    # SB configured but inat_submit_enabled defaults to False.
    settings = Settings(
        env="local", service_bus_namespace="dragonfly-sb-test.servicebus.windows.net"
    )

    result = await process_pending_photo(
        fake_session,
        storage,
        moderator,
        bucket=_BUCKET,
        object_name=_OBJECT_NAME,
        settings=settings,
    )
    assert result.decision == "clean"
    assert result.outbox_status is None
    assert obs.moderation_status == "clean"
    fake_session.add.assert_not_called()


async def test_clean_with_inat_enabled_and_disabled_sb_leaves_outbox_pending(
    fake_session: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When `inat_submit_enabled=True` but Service Bus is not yet
    provisioned, the outbox row IS written (the rollout window where
    the operator flipped the flag before populating
    `service_bus_namespace`) and the enqueue helper returns
    not_configured -- the row stays pending for the replay job."""
    from app.core.config import Settings
    from app.inat.enqueue import InatEnqueueResult

    photo = _photo_row()
    obs = _obs_row()
    _wire_session_with_outbox_update(fake_session, photo=photo, observation=obs)
    storage = _StubStorage()
    moderator = _StubModerator(ModerationResult(decision="clean"))

    enqueue_calls: list[str] = []

    async def fake_enqueue(observation_id: str, *, settings: Settings) -> InatEnqueueResult:
        enqueue_calls.append(observation_id)
        return InatEnqueueResult(success=False, reason="not_configured")

    monkeypatch.setattr("app.moderation.processor.enqueue_inat_submit", fake_enqueue)

    settings = Settings(env="local", service_bus_namespace="", inat_submit_enabled=True)

    result = await process_pending_photo(
        fake_session,
        storage,
        moderator,
        bucket=_BUCKET,
        object_name=_OBJECT_NAME,
        settings=settings,
    )
    assert result.decision == "clean"
    assert result.outbox_status == "pending"
    assert enqueue_calls == [_OBS_ID]
    # Two commits: the in-transaction outbox commit + the post-enqueue
    # retry-count bump commit.
    assert fake_session.commit.await_count == 2


async def test_clean_with_inat_enabled_and_successful_enqueue_flips_outbox(
    fake_session: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Phase-3 happy path: `inat_submit_enabled=True` + SB configured +
    enqueue success -> outbox row flips to `enqueued`."""
    from app.core.config import Settings
    from app.inat.enqueue import InatEnqueueResult

    photo = _photo_row()
    obs = _obs_row()
    _wire_session_with_outbox_update(fake_session, photo=photo, observation=obs)
    storage = _StubStorage()
    moderator = _StubModerator(ModerationResult(decision="clean"))

    async def fake_enqueue(observation_id: str, *, settings: Settings) -> InatEnqueueResult:
        return InatEnqueueResult(success=True)

    monkeypatch.setattr("app.moderation.processor.enqueue_inat_submit", fake_enqueue)

    settings = Settings(
        env="local",
        service_bus_namespace="dragonfly-sb-test.servicebus.windows.net",
        inat_submit_enabled=True,
    )

    result = await process_pending_photo(
        fake_session,
        storage,
        moderator,
        bucket=_BUCKET,
        object_name=_OBJECT_NAME,
        settings=settings,
    )
    assert result.decision == "clean"
    assert result.outbox_status == "enqueued"


async def test_flagged_path_sets_moderation_status_quarantine(
    fake_session: AsyncMock,
) -> None:
    """Flagged decisions also flip observation.moderation_status; no outbox."""
    photo = _photo_row()
    obs = _obs_row()
    _wire_session(fake_session, photo=photo, observation=obs)
    storage = _StubStorage()
    moderator = _StubModerator(ModerationResult(decision="flagged", labels={"adult": "LIKELY"}))

    result = await process_pending_photo(
        fake_session,
        storage,
        moderator,
        bucket=_BUCKET,
        object_name=_OBJECT_NAME,
    )
    assert result.decision == "flagged"
    assert result.outbox_status is None
    assert obs.moderation_status == "quarantine"
    assert obs.moderation_labels == {"adult": "LIKELY"}


async def test_unavailable_bubbles_up_unchanged(fake_session: AsyncMock) -> None:
    _wire_session(fake_session, photo=_photo_row())
    storage = _StubStorage()
    moderator = _StubModerator(ModerationUnavailable("vision down"))

    with pytest.raises(ModerationUnavailable):
        await process_pending_photo(
            fake_session,
            storage,
            moderator,
            bucket=_BUCKET,
            object_name=_OBJECT_NAME,
        )
    # No GCS moves, no DB commit
    assert storage.copy_calls == []
    assert storage.delete_calls == []
    fake_session.commit.assert_not_called()
