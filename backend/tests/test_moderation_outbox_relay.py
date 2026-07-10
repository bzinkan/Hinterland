from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from admin.moderation_outbox_relay import relay
from app.core.config import Settings
from app.db import models
from app.moderation.enqueue import ModerationEnqueueResult


@pytest.fixture
def fake_session() -> AsyncMock:
    return AsyncMock(spec=AsyncSession)


def _rows() -> list[tuple[models.ModerationOutbox, models.Photo]]:
    outbox = models.ModerationOutbox(
        observation_id="01J0OBSID00000000000000ULID",
        photo_id="01J0PHOTOID00000000000ULID",
        status="pending",
        retry_count=0,
        created_at=datetime.now(UTC) - timedelta(minutes=5),
    )
    photo = models.Photo(
        id=outbox.photo_id,
        user_id="user-id",
        bucket="photos",
        object_name=f"pending/uploads/{outbox.photo_id}.jpg",
        canonical_object_name=f"pending/finalized/{outbox.photo_id}.jpg",
        status="pending",
        attachment_status="attached",
        verified_at=datetime.now(UTC),
    )
    return [(outbox, photo)]


async def test_relay_uses_only_committed_outbox_rows(
    fake_session: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = _rows()
    result = MagicMock()
    result.all.return_value = rows
    fake_session.execute = AsyncMock(return_value=result)
    fake_session.commit = AsyncMock()
    captured: dict[str, object] = {}

    async def fake_enqueue(**kwargs: object) -> ModerationEnqueueResult:
        captured.update(kwargs)
        return ModerationEnqueueResult(success=True)

    monkeypatch.setattr(
        "admin.moderation_outbox_relay.enqueue_moderation_work",
        fake_enqueue,
    )
    settings = Settings(service_bus_namespace="test.servicebus.windows.net")

    assert await relay(fake_session, settings) == 1
    assert captured["observation_id"] == rows[0][0].observation_id
    assert captured["object_name"] == rows[0][1].canonical_object_name
    assert rows[0][0].status == "enqueued"
    statement = str(fake_session.execute.await_args.args[0])
    assert "photos.attachment_status" in statement
    assert "photos.canonical_object_name IS NOT NULL" in statement
    assert "photos.verified_at IS NOT NULL" in statement


async def test_relay_does_not_query_when_service_bus_disabled(fake_session: AsyncMock) -> None:
    assert await relay(fake_session, Settings(service_bus_namespace="")) == 0
    fake_session.execute.assert_not_awaited()
