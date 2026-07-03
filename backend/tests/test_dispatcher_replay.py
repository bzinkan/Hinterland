"""Tests for admin/dispatcher_replay.py."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from admin.dispatcher_replay import replay
from app.db import models


def _user() -> models.User:
    return models.User(id="u1", firebase_uid="fb-1", role="kid", display_name="K")


def _group() -> models.Group:
    return models.Group(id="g1", name="Family", join_code="ABC123", owner_user_id="u1")


def _obs() -> models.Observation:
    obs = models.Observation(
        id="o1",
        user_id="u1",
        group_id="g1",
        photo_id="p1",
        latitude=39.1,
        longitude=-84.5,
        dispatched_at=None,
    )
    obs.created_at = datetime.now(UTC) - timedelta(minutes=10)
    return obs


def _photo() -> models.Photo:
    return models.Photo(
        id="p1", user_id="u1", bucket="b", object_name="observations/p1.jpg", status="clean"
    )


def _wire(
    fake_session: AsyncMock,
    *,
    rows: list[tuple[models.Observation, models.User, models.Group, models.Photo]],
    handler_raises: bool = False,
) -> None:
    """Mock the SELECT, then for each row mock whatever execute() calls
    the dispatcher's handlers issue. We stub HANDLERS to a single
    no-op handler so we don't have to mock every per-handler query."""
    list_result = MagicMock()
    list_result.all = MagicMock(return_value=rows)
    side_effects: list[Any] = [list_result]
    fake_session.execute = AsyncMock(side_effect=side_effects)
    fake_session.commit = AsyncMock(side_effect=Exception("boom") if handler_raises else None)
    fake_session.rollback = AsyncMock()


@pytest.fixture
def fake_session() -> AsyncMock:
    return AsyncMock(spec=AsyncSession)


@pytest.fixture(autouse=True)
def _stub_handlers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace HANDLERS with an empty list so dispatch() is a no-op
    that doesn't issue any DB queries -- lets us test the replay
    orchestration without mocking the per-handler query sequences."""
    import admin.dispatcher_replay as mod

    monkeypatch.setattr(mod, "HANDLERS", [])


async def test_replay_no_rows_returns_zero(fake_session: AsyncMock) -> None:
    _wire(fake_session, rows=[])
    count = await replay(fake_session)
    assert count == 0
    fake_session.commit.assert_not_called()


async def test_replay_stamps_dispatched_at_on_success(fake_session: AsyncMock) -> None:
    obs = _obs()
    _wire(fake_session, rows=[(obs, _user(), _group(), _photo())])

    count = await replay(fake_session)
    assert count == 1
    assert obs.dispatched_at is not None
    fake_session.commit.assert_awaited_once()


async def test_replay_keeps_identified_row_eligible_when_world_fails(
    fake_session: AsyncMock,
) -> None:
    """dispatch() never raises for handler failures, so an unconditional
    stamp would end retries for an observation whose Sanctuary write
    failed -- and replay is the only delivery path for contributions
    repaired by migration 20260703_0009. With HANDLERS stubbed empty,
    ctx.results has no world entry, which the guard treats as failure
    for identified observations."""
    obs = _obs()
    obs.taxon_id = 12345
    _wire(fake_session, rows=[(obs, _user(), _group(), _photo())])

    count = await replay(fake_session)
    assert count == 0
    assert obs.dispatched_at is None
    # The row's transaction still commits (handler side effects persist).
    fake_session.commit.assert_awaited_once()


async def test_replay_stamps_identified_row_when_world_healthy(
    fake_session: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import admin.dispatcher_replay as mod
    from app.dispatcher.types import HandlerResult

    async def fake_dispatch(ctx: Any, handlers: Any) -> list[Any]:
        ctx.results["world"] = HandlerResult(
            rewards=[], state={"contribution_id": "o1", "replay": False}
        )
        return []

    monkeypatch.setattr(mod, "dispatch", fake_dispatch)

    obs = _obs()
    obs.taxon_id = 12345
    _wire(fake_session, rows=[(obs, _user(), _group(), _photo())])

    count = await replay(fake_session)
    assert count == 1
    assert obs.dispatched_at is not None


async def test_replay_handles_dispatch_failure_per_row(fake_session: AsyncMock) -> None:
    """If commit raises (simulating handler chaos), rollback is issued
    per row and the replay count stays at 0. The in-memory model may
    have a `dispatched_at` set just before the failed commit, but
    that's discarded by the rollback at the DB level -- which is
    what the next replay run will see."""
    obs1 = _obs()
    obs2 = _obs()
    obs2.id = "o2"
    _wire(
        fake_session,
        rows=[(obs1, _user(), _group(), _photo()), (obs2, _user(), _group(), _photo())],
        handler_raises=True,
    )

    count = await replay(fake_session)
    assert count == 0  # neither succeeded
    # Rollback called for each row that failed.
    assert fake_session.rollback.await_count == 2
