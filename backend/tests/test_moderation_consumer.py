"""Unit tests for the Service Bus moderation consumer.

Covers the parser + the per-message handler (`process_one`). The
receive loop itself is intentionally not unit-tested -- it's a thin
wrapper around `azure.servicebus.aio.ServiceBusReceiver` and is
exercised by the Stream D infra smoke (`az containerapp job start`).
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from admin.moderation_consumer import (
    _message_body_to_text,
    _MessageParseError,
    parse_moderation_work_payload,
    process_one,
)
from app.core.config import Settings
from app.db import models
from app.moderation.processor import ProcessResult
from app.moderation.provider import ModerationResult, ModerationUnavailable

_BUCKET = "photos"
_OBJECT_NAME = "pending/finalized/01J0OBSID00000000000000ULID.jpg"
_OBS_ID = "01J0OBSID00000000000000ULID"
_PHOTO_ID = "01J0PHOTOID00000000000ULID"


class _MessageWithBody:
    def __init__(self, body: object) -> None:
        self.body = body


# ---------------------------------------------------------------------------
# parse_moderation_work_payload
# ---------------------------------------------------------------------------


def test_parse_accepts_committed_work_envelope() -> None:
    body = json.dumps(
        {
            "observation_id": _OBS_ID,
            "photo_id": _PHOTO_ID,
            "bucket": _BUCKET,
            "object_name": _OBJECT_NAME,
        }
    )
    out = parse_moderation_work_payload(body)
    assert out.observation_id == _OBS_ID
    assert out.photo_id == _PHOTO_ID
    assert out.bucket == _BUCKET
    assert out.object_name == _OBJECT_NAME


def test_parse_rejects_legacy_blob_created_event() -> None:
    body = json.dumps(
        {
            "type": "Microsoft.Storage.BlobCreated",
            "subject": f"/blobServices/default/containers/{_BUCKET}/blobs/{_OBJECT_NAME}",
        }
    )
    with pytest.raises(_MessageParseError):
        parse_moderation_work_payload(body)


def test_parse_raises_on_non_json() -> None:
    with pytest.raises(_MessageParseError):
        parse_moderation_work_payload("<not-json>")


def test_parse_raises_when_json_is_not_object() -> None:
    with pytest.raises(_MessageParseError):
        parse_moderation_work_payload(json.dumps([{"subject": "x"}]))


def test_message_body_to_text_decodes_byte_iterable_body() -> None:
    message = _MessageWithBody([b'{"subject": "abc"}'])
    assert _message_body_to_text(message) == '{"subject": "abc"}'


def test_parse_raises_when_identifiers_missing() -> None:
    body = json.dumps({"observation_id": _OBS_ID})
    with pytest.raises(_MessageParseError):
        parse_moderation_work_payload(body)


def test_parse_raises_on_non_pending_object() -> None:
    body = json.dumps(
        {
            "observation_id": _OBS_ID,
            "photo_id": _PHOTO_ID,
            "bucket": _BUCKET,
            "object_name": "observations/public.jpg",
        }
    )
    with pytest.raises(_MessageParseError):
        parse_moderation_work_payload(body)


# ---------------------------------------------------------------------------
# process_one
# ---------------------------------------------------------------------------


class _StubStorage:
    def generate_put_url(self, **_: Any) -> tuple[str, Any]:
        raise NotImplementedError

    def fetch_object_bytes(self, *, bucket: str, object_name: str) -> bytes:
        raise NotImplementedError

    def copy_object(self, **_: Any) -> None:  # pragma: no cover - unused here
        raise NotImplementedError

    def delete_object(self, **_: Any) -> None:  # pragma: no cover - unused here
        raise NotImplementedError

    def generate_get_url(self, **_: object) -> tuple[str, object]:
        raise NotImplementedError


class _StubModerator:
    async def moderate(self, image_bytes: bytes) -> ModerationResult:
        return ModerationResult(decision="clean")


@pytest.fixture
def fake_session() -> AsyncMock:
    return AsyncMock(spec=AsyncSession)


def _good_body() -> str:
    return json.dumps(
        {
            "observation_id": _OBS_ID,
            "photo_id": _PHOTO_ID,
            "bucket": _BUCKET,
            "object_name": _OBJECT_NAME,
        }
    )


def _settings() -> Settings:
    return Settings(env="local", service_bus_namespace="")


def _wire_outbox(
    fake_session: AsyncMock,
    *,
    status: str = "enqueued",
    photo_id: str = _PHOTO_ID,
    retry_count: int = 0,
) -> models.ModerationOutbox:
    outbox = models.ModerationOutbox(
        observation_id=_OBS_ID,
        photo_id=photo_id,
        status=status,
        retry_count=retry_count,
    )
    result = MagicMock()
    result.scalar_one_or_none.return_value = outbox
    fake_session.execute = AsyncMock(return_value=result)
    fake_session.commit = AsyncMock()
    return outbox


async def test_process_one_dead_letters_on_parse_failure(fake_session: AsyncMock) -> None:
    result = await process_one(
        fake_session,
        _StubStorage(),  # type: ignore[arg-type]
        _StubModerator(),  # type: ignore[arg-type]
        _settings(),
        body="garbage",
    )
    assert result == "dead_letter"


async def test_process_one_abandons_on_photo_not_found(
    fake_session: AsyncMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    from admin import moderation_consumer

    async def boom(*_args: Any, **_kw: Any) -> ProcessResult:
        from app.moderation.processor import PhotoNotFound

        raise PhotoNotFound("photo missing")

    monkeypatch.setattr(moderation_consumer, "process_pending_photo", boom)
    outbox = _wire_outbox(fake_session)

    result = await process_one(
        fake_session,
        _StubStorage(),  # type: ignore[arg-type]
        _StubModerator(),  # type: ignore[arg-type]
        _settings(),
        body=_good_body(),
    )
    assert result == "abandon"
    assert outbox.status == "failed"


async def test_process_one_abandons_on_moderator_unavailable(
    fake_session: AsyncMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    from admin import moderation_consumer

    async def boom(*_args: Any, **_kw: Any) -> ProcessResult:
        raise ModerationUnavailable("vision down")

    monkeypatch.setattr(moderation_consumer, "process_pending_photo", boom)
    outbox = _wire_outbox(fake_session)

    result = await process_one(
        fake_session,
        _StubStorage(),  # type: ignore[arg-type]
        _StubModerator(),  # type: ignore[arg-type]
        _settings(),
        body=_good_body(),
    )
    assert result == "abandon"
    assert outbox.status == "failed"


async def test_process_one_dead_letters_after_fifth_transient_failure(
    fake_session: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from admin import moderation_consumer

    async def boom(*_args: Any, **_kw: Any) -> ProcessResult:
        raise ModerationUnavailable("provider down")

    monkeypatch.setattr(moderation_consumer, "process_pending_photo", boom)
    outbox = _wire_outbox(fake_session, retry_count=4)

    result = await process_one(
        fake_session,
        _StubStorage(),  # type: ignore[arg-type]
        _StubModerator(),  # type: ignore[arg-type]
        _settings(),
        body=_good_body(),
    )

    assert result == "dead_letter"
    assert outbox.status == "dlq"
    assert outbox.retry_count == 5


async def test_process_one_completes_on_clean_decision(
    fake_session: AsyncMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    from admin import moderation_consumer

    captured: dict[str, Any] = {}
    outbox = _wire_outbox(fake_session)

    async def fake_processor(
        session: AsyncSession,
        storage: Any,
        moderator: Any,
        *,
        bucket: str,
        object_name: str,
        settings: Settings | None = None,
        expected_photo_id: str | None = None,
        expected_observation_id: str | None = None,
    ) -> ProcessResult:
        captured.update(
            bucket=bucket,
            object_name=object_name,
            settings=settings,
            expected_photo_id=expected_photo_id,
            expected_observation_id=expected_observation_id,
        )
        # The real processor completes this row in the same commit as the
        # destination/photo/observation transition.
        outbox.status = "succeeded"
        outbox.lease_until = None
        return ProcessResult(
            photo_id="p",
            decision="clean",
            new_object_name=f"observations/{object_name.split('/')[-1]}",
            review_queue_id=None,
            observation_id="o",
            outbox_status="enqueued",
        )

    monkeypatch.setattr(moderation_consumer, "process_pending_photo", fake_processor)

    result = await process_one(
        fake_session,
        _StubStorage(),  # type: ignore[arg-type]
        _StubModerator(),  # type: ignore[arg-type]
        _settings(),
        body=_good_body(),
    )
    assert result == "complete"
    assert captured["bucket"] == _BUCKET
    assert captured["object_name"] == _OBJECT_NAME
    assert isinstance(captured["settings"], Settings)
    assert captured["expected_photo_id"] == _PHOTO_ID
    assert captured["expected_observation_id"] == _OBS_ID
    assert outbox.status == "succeeded"
    claim_update = str(fake_session.execute.await_args_list[1].args[0])
    assert "moderation_status NOT IN" in claim_update
    # Only the processing-lease commit belongs to the consumer. There is no
    # second, crash-prone consumer terminal commit.
    fake_session.commit.assert_awaited_once()


async def test_process_one_duplicate_succeeded_delivery_is_harmless(
    fake_session: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from admin import moderation_consumer

    processor = AsyncMock()
    monkeypatch.setattr(moderation_consumer, "process_pending_photo", processor)
    _wire_outbox(fake_session, status="succeeded")

    result = await process_one(
        fake_session,
        _StubStorage(),  # type: ignore[arg-type]
        _StubModerator(),  # type: ignore[arg-type]
        _settings(),
        body=_good_body(),
    )

    assert result == "complete"
    processor.assert_not_awaited()
    fake_session.commit.assert_not_awaited()


async def test_process_one_dead_letters_without_committed_outbox(
    fake_session: AsyncMock,
) -> None:
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    fake_session.execute = AsyncMock(return_value=result)

    disposition = await process_one(
        fake_session,
        _StubStorage(),  # type: ignore[arg-type]
        _StubModerator(),  # type: ignore[arg-type]
        _settings(),
        body=_good_body(),
    )

    assert disposition == "dead_letter"


async def test_consume_no_op_when_service_bus_disabled(
    fake_session: AsyncMock,
) -> None:
    """When namespace is empty the consume loop logs + returns 0 without
    touching the Service Bus SDK at all."""
    from admin.moderation_consumer import consume

    sessions = MagicMock()  # never called
    storage = _StubStorage()
    moderator = _StubModerator()

    processed = await consume(
        _settings(),
        sessions,  # type: ignore[arg-type]
        storage,  # type: ignore[arg-type]
        moderator,  # type: ignore[arg-type]
        max_messages=10,
    )
    assert processed == 0
