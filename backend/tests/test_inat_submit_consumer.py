"""Unit tests for the Service Bus iNat-submit consumer.

Covers the parser + each per-message disposition path
(``complete`` / ``abandon`` / ``dead_letter``). The Service Bus
receive loop is exercised by Stream D infra smoke, not here.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from admin.inat_submit_consumer import (
    _message_body_to_text,
    _MessageParseError,
    parse_inat_submit_payload,
    process_one,
)
from app.core.config import Settings
from app.db import models
from app.inat.client import InatUnavailable
from app.inat.submit import InatSubmitResult

_OBS_ID = "01J0OBSID00000000000000ULID"
_PHOTO_ID = "01J0PHOTOID00000000000ULID"


class _MessageWithBody:
    def __init__(self, body: object) -> None:
        self.body = body


# ---------------------------------------------------------------------------
# parse_inat_submit_payload
# ---------------------------------------------------------------------------


def test_parse_extracts_observation_id() -> None:
    out = parse_inat_submit_payload(json.dumps({"observation_id": _OBS_ID}))
    assert out == _OBS_ID


def test_parse_raises_on_non_json() -> None:
    with pytest.raises(_MessageParseError):
        parse_inat_submit_payload("not-json")


def test_parse_raises_when_observation_id_missing() -> None:
    with pytest.raises(_MessageParseError):
        parse_inat_submit_payload(json.dumps({"other_field": "x"}))


def test_parse_raises_when_observation_id_empty() -> None:
    with pytest.raises(_MessageParseError):
        parse_inat_submit_payload(json.dumps({"observation_id": ""}))


def test_parse_raises_when_payload_is_list() -> None:
    with pytest.raises(_MessageParseError):
        parse_inat_submit_payload(json.dumps([{"observation_id": _OBS_ID}]))


def test_message_body_to_text_decodes_byte_iterable_body() -> None:
    message = _MessageWithBody([json.dumps({"observation_id": _OBS_ID}).encode("utf-8")])
    assert _message_body_to_text(message) == json.dumps({"observation_id": _OBS_ID})


# ---------------------------------------------------------------------------
# process_one helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_session() -> AsyncMock:
    return AsyncMock(spec=AsyncSession)


def _obs_row(
    *,
    moderation_status: str = "clean",
    inat_observation_id: int | None = None,
) -> models.Observation:
    return models.Observation(
        id=_OBS_ID,
        user_id="u",
        group_id="g",
        photo_id=_PHOTO_ID,
        latitude=39.1,
        longitude=-84.5,
        created_at=datetime(2026, 6, 4, 12, 0, 0, tzinfo=UTC),
        moderation_status=moderation_status,
        inat_observation_id=inat_observation_id,
    )


def _photo_row(status: str = "clean") -> models.Photo:
    return models.Photo(
        id=_PHOTO_ID,
        user_id="u",
        bucket="photos",
        object_name=f"observations/{_PHOTO_ID}.jpg",
        status=status,
        content_type="image/jpeg",
    )


def _wire_session(
    fake_session: AsyncMock,
    *,
    obs: models.Observation | None,
    photo: models.Photo | None = None,
    extra_updates: int = 0,
) -> None:
    """Sequence the session.execute side_effects.

    Calls (in order, depending on path):
      1. select(Observation)            -- always
      2. select(Photo)                  -- only if obs is clean and not yet submitted
      3+ update(InatSubmitOutbox)       -- 1 or 2 depending on path
    """
    obs_result = MagicMock()
    obs_result.scalar_one_or_none = MagicMock(return_value=obs)
    photo_result = MagicMock()
    photo_result.scalar_one_or_none = MagicMock(return_value=photo)
    update_result = MagicMock()
    side_effects: list[Any] = [obs_result]
    if photo is not None:
        side_effects.append(photo_result)
    for _ in range(extra_updates):
        side_effects.append(update_result)
    fake_session.execute = AsyncMock(side_effect=side_effects)
    fake_session.commit = AsyncMock()


class _StubStorage:
    def __init__(self, *, raises: Exception | None = None, body: bytes = b"jpeg"):
        self._raises = raises
        self._body = body

    def generate_put_url(self, **_: Any) -> tuple[str, Any]:
        raise NotImplementedError

    def fetch_object_bytes(self, *, bucket: str, object_name: str) -> bytes:
        if self._raises is not None:
            raise self._raises
        return self._body

    def copy_object(self, **_: Any) -> None:  # pragma: no cover - unused here
        raise NotImplementedError

    def delete_object(self, **_: Any) -> None:  # pragma: no cover - unused here
        raise NotImplementedError

    def generate_get_url(self, **_: object) -> tuple[str, object]:
        raise NotImplementedError


def _body() -> str:
    return json.dumps({"observation_id": _OBS_ID})


# ---------------------------------------------------------------------------
# process_one
# ---------------------------------------------------------------------------


async def test_process_one_dead_letters_on_parse_failure(fake_session: AsyncMock) -> None:
    result = await process_one(
        fake_session,
        _StubStorage(),  # type: ignore[arg-type]
        AsyncMock(),
        body="garbage",
    )
    assert result == "dead_letter"


async def test_process_one_dead_letters_when_observation_missing(
    fake_session: AsyncMock,
) -> None:
    _wire_session(fake_session, obs=None, extra_updates=1)
    result = await process_one(
        fake_session,
        _StubStorage(),  # type: ignore[arg-type]
        AsyncMock(),
        body=_body(),
    )
    assert result == "dead_letter"


async def test_process_one_dead_letters_when_observation_not_clean(
    fake_session: AsyncMock,
) -> None:
    _wire_session(fake_session, obs=_obs_row(moderation_status="quarantine"), extra_updates=1)
    result = await process_one(
        fake_session,
        _StubStorage(),  # type: ignore[arg-type]
        AsyncMock(),
        body=_body(),
    )
    assert result == "dead_letter"


async def test_process_one_completes_idempotent_when_already_submitted(
    fake_session: AsyncMock,
) -> None:
    """A duplicate Service Bus delivery for an already-submitted obs flips
    the outbox row to ``submitted`` (in case it lingered) and returns
    complete -- never double-submits to iNat."""
    _wire_session(
        fake_session,
        obs=_obs_row(inat_observation_id=12345),
        extra_updates=1,
    )
    result = await process_one(
        fake_session,
        _StubStorage(),  # type: ignore[arg-type]
        AsyncMock(),
        body=_body(),
    )
    assert result == "complete"


async def test_process_one_dead_letters_when_photo_missing(
    fake_session: AsyncMock,
) -> None:
    # obs exists, but no photo row (PhotoNotFound shape)
    _wire_session(fake_session, obs=_obs_row(), photo=None, extra_updates=1)
    # The wire sequence above only provides the obs_result + 1 update; we
    # need to provide a photo_result that resolves to None as well.
    obs_result = MagicMock()
    obs_result.scalar_one_or_none = MagicMock(return_value=_obs_row())
    photo_result = MagicMock()
    photo_result.scalar_one_or_none = MagicMock(return_value=None)
    update_result = MagicMock()
    fake_session.execute = AsyncMock(side_effect=[obs_result, photo_result, update_result])
    fake_session.commit = AsyncMock()
    result = await process_one(
        fake_session,
        _StubStorage(),  # type: ignore[arg-type]
        AsyncMock(),
        body=_body(),
    )
    assert result == "dead_letter"


async def test_process_one_abandons_on_inat_unavailable(
    fake_session: AsyncMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    from admin import inat_submit_consumer

    obs = _obs_row()
    photo = _photo_row()
    obs_result = MagicMock()
    obs_result.scalar_one_or_none = MagicMock(return_value=obs)
    photo_result = MagicMock()
    photo_result.scalar_one_or_none = MagicMock(return_value=photo)
    update_result = MagicMock()
    fake_session.execute = AsyncMock(side_effect=[obs_result, photo_result, update_result])
    fake_session.commit = AsyncMock()

    async def boom(*_args: Any, **_kw: Any) -> InatSubmitResult:
        raise InatUnavailable("iNat 503")

    monkeypatch.setattr(inat_submit_consumer, "submit_observation_to_inat", boom)

    result = await process_one(
        fake_session,
        _StubStorage(),  # type: ignore[arg-type]
        AsyncMock(),
        body=_body(),
    )
    assert result == "abandon"


async def test_process_one_completes_and_updates_observation_on_success(
    fake_session: AsyncMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    from admin import inat_submit_consumer

    obs = _obs_row()
    photo = _photo_row()
    obs_result = MagicMock()
    obs_result.scalar_one_or_none = MagicMock(return_value=obs)
    photo_result = MagicMock()
    photo_result.scalar_one_or_none = MagicMock(return_value=photo)
    update_result = MagicMock()
    fake_session.execute = AsyncMock(side_effect=[obs_result, photo_result, update_result])
    fake_session.commit = AsyncMock()

    async def fake_submit(*_args: Any, **_kw: Any) -> InatSubmitResult:
        return InatSubmitResult(inat_observation_id=999, inat_uuid="uuid-999")

    monkeypatch.setattr(inat_submit_consumer, "submit_observation_to_inat", fake_submit)

    result = await process_one(
        fake_session,
        _StubStorage(),  # type: ignore[arg-type]
        AsyncMock(),
        body=_body(),
    )
    assert result == "complete"
    assert obs.inat_observation_id == 999
    assert obs.submitted_to_inat_at is not None


async def test_consume_no_op_when_service_bus_disabled() -> None:
    from admin.inat_submit_consumer import consume

    settings = Settings(env="local", service_bus_namespace="")
    processed = await consume(
        settings,
        MagicMock(),  # type: ignore[arg-type]
        _StubStorage(),  # type: ignore[arg-type]
        AsyncMock(),
        max_messages=10,
    )
    assert processed == 0
