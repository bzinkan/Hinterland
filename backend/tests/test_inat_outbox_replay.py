"""Unit tests for the 15-min iNat outbox replay job.

Mocks the SQLAlchemy session + the enqueue helper. The cron-side
plumbing (Container Apps Job + scheduler) lives in PR 5 infra-azure
scripts and is verified via the Stream D smoke (`az containerapp
job start --name hinterland-inat-outbox-replay`).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from admin.inat_outbox_replay import replay
from app.core.config import Settings
from app.db import models
from app.inat.enqueue import InatEnqueueResult


def _settings_with_sb() -> Settings:
    return Settings(
        env="local",
        service_bus_namespace="hinterland-sb-test.servicebus.windows.net",
        inat_submit_enabled=True,
    )


def _settings_without_sb() -> Settings:
    return Settings(env="local", service_bus_namespace="", inat_submit_enabled=True)


def _outbox_row(
    *,
    observation_id: str = "01J0OBSID00000000000000ULID",
    status: str = "pending",
    retry_count: int = 0,
    last_attempt_at: datetime | None = None,
    created_at: datetime | None = None,
) -> models.InatSubmitOutbox:
    return models.InatSubmitOutbox(
        observation_id=observation_id,
        status=status,
        retry_count=retry_count,
        last_attempt_at=last_attempt_at,
        created_at=created_at or (datetime.now(UTC) - timedelta(hours=1)),
    )


@pytest.fixture
def fake_session() -> AsyncMock:
    return AsyncMock(spec=AsyncSession)


def _wire_select(fake_session: AsyncMock, *, rows: list[models.InatSubmitOutbox]) -> None:
    """First execute call returns the SELECT result; subsequent calls
    are UPDATE results (1 per row)."""
    select_result = MagicMock()
    scalars = MagicMock()
    scalars.all = MagicMock(return_value=rows)
    select_result.scalars = MagicMock(return_value=scalars)

    side_effects: list[Any] = [select_result]
    # One UPDATE per row processed.
    side_effects.extend(MagicMock() for _ in rows)

    fake_session.execute = AsyncMock(side_effect=side_effects)
    fake_session.commit = AsyncMock()


async def test_replay_returns_zero_when_no_pending_rows(fake_session: AsyncMock) -> None:
    _wire_select(fake_session, rows=[])
    count = await replay(fake_session, _settings_with_sb())
    assert count == 0
    fake_session.commit.assert_not_called()


async def test_replay_disabled_does_not_read_or_enqueue(fake_session: AsyncMock) -> None:
    settings = Settings(
        env="local",
        service_bus_namespace="hinterland-sb-test.servicebus.windows.net",
        inat_submit_enabled=False,
    )

    assert await replay(fake_session, settings) == 0
    fake_session.execute.assert_not_awaited()


async def test_replay_flips_pending_to_enqueued_on_successful_enqueue(
    fake_session: AsyncMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    row = _outbox_row()
    _wire_select(fake_session, rows=[row])

    async def fake_enqueue(observation_id: str, *, settings: Settings) -> InatEnqueueResult:
        return InatEnqueueResult(success=True)

    monkeypatch.setattr("admin.inat_outbox_replay.enqueue_inat_submit", fake_enqueue)

    count = await replay(fake_session, _settings_with_sb())
    assert count == 1
    # One SELECT + one UPDATE.
    assert fake_session.execute.await_count == 2
    fake_session.commit.assert_awaited_once()


async def test_replay_leaves_row_pending_when_enqueue_fails(
    fake_session: AsyncMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    row = _outbox_row()
    _wire_select(fake_session, rows=[row])

    async def fake_enqueue(observation_id: str, *, settings: Settings) -> InatEnqueueResult:
        return InatEnqueueResult(success=False, reason="send_failed")

    monkeypatch.setattr("admin.inat_outbox_replay.enqueue_inat_submit", fake_enqueue)

    count = await replay(fake_session, _settings_with_sb())
    assert count == 0
    # Still commits the retry_count + last_error bump.
    fake_session.commit.assert_awaited_once()


async def test_replay_handles_mixed_success_and_failure(
    fake_session: AsyncMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = [
        _outbox_row(observation_id="01J0OBS_A_0000000000000ULID"),
        _outbox_row(observation_id="01J0OBS_B_0000000000000ULID"),
        _outbox_row(observation_id="01J0OBS_C_0000000000000ULID"),
    ]
    _wire_select(fake_session, rows=rows)

    # B fails; A and C succeed. Match the `_B_` separator so "B" inside
    # the literal "OBS" doesn't false-positive on every row.
    async def fake_enqueue(observation_id: str, *, settings: Settings) -> InatEnqueueResult:
        if "_B_" in observation_id:
            return InatEnqueueResult(success=False, reason="send_failed")
        return InatEnqueueResult(success=True)

    monkeypatch.setattr("admin.inat_outbox_replay.enqueue_inat_submit", fake_enqueue)

    count = await replay(fake_session, _settings_with_sb())
    assert count == 2
    assert fake_session.commit.await_count == 3


async def test_replay_no_op_when_service_bus_disabled(
    fake_session: AsyncMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When service_bus_namespace is empty the enqueue helper returns
    not_configured; the row stays pending; replay returns 0."""
    row = _outbox_row()
    _wire_select(fake_session, rows=[row])

    # No monkeypatch: the real enqueue helper short-circuits on empty
    # namespace and returns not_configured.

    count = await replay(fake_session, _settings_without_sb())
    assert count == 0
    fake_session.commit.assert_awaited_once()  # the retry-count bump


async def test_replay_caps_at_max_per_run(
    fake_session: AsyncMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The SELECT has LIMIT 200 baked in; this test verifies the helper
    consumes all the rows the SELECT returned without unbounded iteration."""
    rows = [_outbox_row(observation_id=f"01J0OBS{i:03d}0000000000000U") for i in range(50)]
    _wire_select(fake_session, rows=rows)

    async def fake_enqueue(observation_id: str, *, settings: Settings) -> InatEnqueueResult:
        return InatEnqueueResult(success=True)

    monkeypatch.setattr("admin.inat_outbox_replay.enqueue_inat_submit", fake_enqueue)

    count = await replay(fake_session, _settings_with_sb())
    assert count == 50
