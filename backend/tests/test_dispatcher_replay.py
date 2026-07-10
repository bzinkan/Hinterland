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
    active_rebuild_id: str | None = None,
    current_rows: list[tuple[models.Observation, models.User, models.Group, models.Photo] | None]
    | None = None,
) -> None:
    """Mock the SELECT, then for each row mock whatever execute() calls
    the dispatcher's handlers issue. We stub HANDLERS to a single
    no-op handler so we don't have to mock every per-handler query."""
    list_result = MagicMock()
    list_result.all = MagicMock(
        return_value=[(observation.id, observation.user_id) for observation, *_ in rows]
    )
    side_effects: list[Any] = [list_result]
    if active_rebuild_id is None:
        for current in current_rows if current_rows is not None else rows:
            current_result = MagicMock()
            current_result.one_or_none = MagicMock(return_value=current)
            side_effects.append(current_result)
    fake_session.execute = AsyncMock(side_effect=side_effects)
    fake_session.scalar = AsyncMock(return_value=active_rebuild_id)
    fake_session.commit = AsyncMock()
    fake_session.rollback = AsyncMock()


@pytest.fixture
def fake_session() -> AsyncMock:
    return AsyncMock(spec=AsyncSession)


@pytest.fixture(autouse=True)
def _stub_handlers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub the durable dispatcher while preserving its public outcome contract."""
    import admin.dispatcher_replay as mod

    monkeypatch.setattr(mod, "HANDLERS", [])
    monkeypatch.setattr(mod, "acquire_user_lock", AsyncMock())

    async def fake_dispatch(ctx: Any, _handlers: object) -> list[object]:
        ctx.observation.dispatch_status = "complete"
        ctx.observation.dispatched_at = datetime.now(UTC)
        await ctx.db.commit()
        return []

    monkeypatch.setattr(mod, "dispatch", fake_dispatch)


async def test_replay_no_rows_returns_zero(fake_session: AsyncMock) -> None:
    _wire(fake_session, rows=[])
    count = await replay(fake_session)
    assert count == 0
    fake_session.commit.assert_not_called()
    statement = str(fake_session.execute.await_args.args[0])
    assert "derived_state_rebuilds" in statement
    assert "NOT (EXISTS" in statement


async def test_replay_stamps_dispatched_at_on_success(fake_session: AsyncMock) -> None:
    obs = _obs()
    _wire(fake_session, rows=[(obs, _user(), _group(), _photo())])

    count = await replay(fake_session)
    assert count == 1
    assert obs.dispatched_at is not None
    fake_session.commit.assert_awaited_once()


async def test_replay_handles_dispatch_failure_per_row(
    fake_session: AsyncMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Infrastructure failures roll back per candidate and do not stop the batch."""
    import admin.dispatcher_replay as mod

    async def failing_dispatch(_ctx: Any, _handlers: object) -> list[object]:
        raise RuntimeError("boom")

    monkeypatch.setattr(mod, "dispatch", failing_dispatch)
    obs1 = _obs()
    obs2 = _obs()
    obs2.id = "o2"
    _wire(
        fake_session,
        rows=[(obs1, _user(), _group(), _photo()), (obs2, _user(), _group(), _photo())],
    )

    count = await replay(fake_session)
    assert count == 0  # neither succeeded
    # Rollback called for each row that failed.
    assert fake_session.rollback.await_count == 2


async def test_replay_defers_candidate_when_rebuild_was_queued_after_select(
    fake_session: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import admin.dispatcher_replay as mod

    dispatch = AsyncMock()
    monkeypatch.setattr(mod, "dispatch", dispatch)
    obs = _obs()
    _wire(
        fake_session,
        rows=[(obs, _user(), _group(), _photo())],
        active_rebuild_id="rebuild-1",
    )

    count = await replay(fake_session)

    assert count == 0
    dispatch.assert_not_awaited()
    mod.acquire_user_lock.assert_awaited_once_with(fake_session, obs.user_id)
    fake_session.commit.assert_awaited_once()


async def test_replay_reloads_and_skips_rejected_state_after_completed_rebuild(
    fake_session: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import admin.dispatcher_replay as mod

    dispatch = AsyncMock()
    monkeypatch.setattr(mod, "dispatch", dispatch)
    candidate = _obs()
    current = _obs()
    current.dispatch_status = "unverified"
    current.moderation_status = "rejected"
    current.rejected_at = datetime.now(UTC)
    current_photo = _photo()
    current_photo.status = "deleted"
    current_photo.attachment_status = "deleted"
    _wire(
        fake_session,
        rows=[(candidate, _user(), _group(), _photo())],
        current_rows=[(current, _user(), _group(), current_photo)],
    )

    count = await replay(fake_session)

    assert count == 0
    dispatch.assert_not_awaited()
    reload_statement = fake_session.execute.await_args_list[1].args[0]
    assert reload_statement.get_execution_options()["populate_existing"] is True
    fake_session.commit.assert_awaited_once()
