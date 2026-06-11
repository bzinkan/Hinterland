"""Unit tests for the full ExpeditionHandler.

Stubs the SQLAlchemy session for the four-query sequence the handler
issues (progress join + species cache + dex + prior obs) plus the
final commit. Uses real expedition JSON dicts so we exercise the
Pydantic round-trip + matcher integration too.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import models
from app.dispatcher.handlers.expedition import ExpeditionHandler
from app.dispatcher.types import Context

_USER_ID = "01J0KIDID0000000000000ULID"
_GROUP_ID = "01J0GROUPID00000000000ULID"
_OBS_ID = "01J0OBSID0000000000000ULID"
_PHOTO_ID = "01J0PHOTOID00000000000ULID"


def _user() -> models.User:
    return models.User(id=_USER_ID, firebase_uid="fb-1", role="kid", display_name="Kid")


def _group() -> models.Group:
    return models.Group(id=_GROUP_ID, name="Family", join_code="ABC123", owner_user_id=_USER_ID)


def _obs(*, taxon_id: int | None = 12345, geohash4: str | None = "dnp1") -> models.Observation:
    obs = models.Observation(
        id=_OBS_ID,
        user_id=_USER_ID,
        group_id=_GROUP_ID,
        photo_id=_PHOTO_ID,
        latitude=39.1,
        longitude=-84.5,
        taxon_id=taxon_id,
        species_name="Northern Cardinal" if taxon_id else None,
        geohash4=geohash4,
    )
    obs.created_at = datetime(2026, 5, 10, 12, 0, 0, tzinfo=UTC)
    return obs


def _photo() -> models.Photo:
    return models.Photo(
        id=_PHOTO_ID,
        user_id=_USER_ID,
        bucket="b",
        object_name=f"observations/{_PHOTO_ID}.jpg",
        status="clean",
    )


def _ctx(fake_session: AsyncMock, *, taxon_id: int | None = 12345) -> Context:
    return Context(
        db=fake_session,
        user=_user(),
        group=_group(),
        observation=_obs(taxon_id=taxon_id),
        photo=_photo(),
    )


def _expedition_body(*, exp_id: str, steps: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "id": exp_id,
        "title": f"Test {exp_id}",
        "tier": 1,
        "duration_minutes": 20,
        "environments": ["yard"],
        "intro": "Find some things.",
        "outro": "You did real science.",
        "prerequisites": [],
        "steps": steps,
    }


def _step(step_id: str, kind: str = "any_organism", **kwargs: Any) -> dict[str, Any]:
    return {
        "id": step_id,
        "description": f"Find a {step_id}",
        "match": {"kind": kind, **kwargs},
    }


def _content(exp_id: str, body: dict[str, Any]) -> models.ExpeditionContent:
    return models.ExpeditionContent(
        id=exp_id,
        tier=body["tier"],
        content_hash="x",
        body=body,
        archived=False,
    )


def _progress(exp_id: str, *, completed: dict[str, Any] | None = None) -> models.ExpeditionProgress:
    return models.ExpeditionProgress(
        id=f"prog-{exp_id}",
        user_id=_USER_ID,
        group_id=_GROUP_ID,
        expedition_id=exp_id,
        completed_steps=completed or {},
        completed_at=None,
    )


def _wire_session(
    fake_session: AsyncMock,
    *,
    progress_pairs: list[tuple[models.ExpeditionProgress, models.ExpeditionContent]],
    species: models.SpeciesCache | None = None,
    dex_taxa: list[int] | None = None,
    prior_obs: list[tuple[float, float]] | None = None,
) -> None:
    """Wire the four-query sequence the handler uses."""
    progress_result = MagicMock()
    progress_result.all = MagicMock(return_value=progress_pairs)

    species_result = MagicMock()
    species_result.scalar_one_or_none = MagicMock(return_value=species)

    dex_result = MagicMock()
    dex_result.all = MagicMock(return_value=[(t,) for t in (dex_taxa or [])])

    prior_result = MagicMock()
    prior_result.all = MagicMock(return_value=prior_obs or [])

    side_effects: list[Any] = [progress_result]
    if progress_pairs:
        # _build_inputs queries species (only if taxon present) + dex + priors
        side_effects.extend([species_result, dex_result, prior_result])

    fake_session.execute = AsyncMock(side_effect=side_effects)
    fake_session.commit = AsyncMock()


@pytest.fixture
def fake_session() -> AsyncMock:
    return AsyncMock(spec=AsyncSession)


# ---------------------------------------------------------------------------


async def test_no_active_expeditions_returns_empty(fake_session: AsyncMock) -> None:
    _wire_session(fake_session, progress_pairs=[])
    handler = ExpeditionHandler()
    result = await handler.handle(_ctx(fake_session))
    assert result.rewards == []
    fake_session.commit.assert_not_called()


async def test_step_advances_when_match_succeeds(fake_session: AsyncMock) -> None:
    body = _expedition_body(
        exp_id="x",
        steps=[_step("first", "any_organism"), _step("second", "any_organism")],
    )
    progress = _progress("x")
    _wire_session(
        fake_session,
        progress_pairs=[(progress, _content("x", body))],
    )

    handler = ExpeditionHandler()
    result = await handler.handle(_ctx(fake_session))

    assert len(result.rewards) == 1
    reward = result.rewards[0]
    assert reward.type == "expedition_step"
    assert reward.payload["step_id"] == "first"
    # Value format: dict carrying the iso timestamp + the crediting
    # observation (the replay-gate key).
    assert progress.completed_steps["first"] == {
        "completed_at": "2026-05-10T12:00:00+00:00",
        "observation_id": _OBS_ID,
    }
    assert progress.completed_at is None  # not the final step
    fake_session.commit.assert_awaited_once()


async def test_final_step_emits_complete_reward(fake_session: AsyncMock) -> None:
    body = _expedition_body(
        exp_id="x",
        steps=[_step("first"), _step("second"), _step("third")],
    )
    progress = _progress(
        "x",
        completed={
            "first": "2026-05-10T11:00:00+00:00",
            "second": "2026-05-10T11:30:00+00:00",
        },
    )
    _wire_session(fake_session, progress_pairs=[(progress, _content("x", body))])

    handler = ExpeditionHandler()
    result = await handler.handle(_ctx(fake_session))

    types = [r.type for r in result.rewards]
    assert types == ["expedition_step", "expedition_complete"]
    assert progress.completed_at is not None
    fake_session.commit.assert_awaited_once()


async def test_no_match_no_advance_no_commit(fake_session: AsyncMock) -> None:
    """Step requires Plantae but observation has no species cache hit
    (so iconic_taxon is unknown). Should not match, no commit."""
    body = _expedition_body(
        exp_id="x",
        steps=[_step("plant", "iconic_taxon", value="Plantae")],
    )
    progress = _progress("x")
    _wire_session(
        fake_session,
        progress_pairs=[(progress, _content("x", body))],
        species=None,  # no iconic_taxon known
    )

    handler = ExpeditionHandler()
    result = await handler.handle(_ctx(fake_session))

    assert result.rewards == []
    assert progress.completed_steps == {}
    fake_session.commit.assert_not_called()


async def test_two_expeditions_can_advance_simultaneously(
    fake_session: AsyncMock,
) -> None:
    """Snapshot scenario 8: one observation advances steps in two expeditions."""
    body_a = _expedition_body(exp_id="a", steps=[_step("first")])
    body_b = _expedition_body(exp_id="b", steps=[_step("first"), _step("second")])
    progress_a = _progress("a")
    progress_b = _progress("b")
    _wire_session(
        fake_session,
        progress_pairs=[
            (progress_a, _content("a", body_a)),
            (progress_b, _content("b", body_b)),
        ],
    )

    handler = ExpeditionHandler()
    result = await handler.handle(_ctx(fake_session))

    # exp_a: step + complete; exp_b: just step (still has 'second' to go)
    types = [r.type for r in result.rewards]
    assert types.count("expedition_step") == 2
    assert types.count("expedition_complete") == 1
    assert progress_a.completed_at is not None
    assert progress_b.completed_at is None
    fake_session.commit.assert_awaited_once()


async def test_already_complete_expedition_skipped(fake_session: AsyncMock) -> None:
    """All steps already done -> handler skips it (defensive against
    races where completed_at is None but completed_steps is full)."""
    body = _expedition_body(exp_id="x", steps=[_step("first")])
    progress = _progress("x", completed={"first": "2026-05-10T10:00:00+00:00"})
    _wire_session(fake_session, progress_pairs=[(progress, _content("x", body))])

    handler = ExpeditionHandler()
    result = await handler.handle(_ctx(fake_session))

    assert result.rewards == []
    fake_session.commit.assert_not_called()


async def test_corrupted_content_body_logged_and_skipped(
    fake_session: AsyncMock,
) -> None:
    """A bad expedition body shouldn't crash the dispatcher -- just skip it."""
    bad_body = {"not": "valid"}  # missing required fields
    content = models.ExpeditionContent(
        id="x", tier=1, content_hash="x", body=bad_body, archived=False
    )
    progress = _progress("x")
    _wire_session(fake_session, progress_pairs=[(progress, content)])

    handler = ExpeditionHandler()
    result = await handler.handle(_ctx(fake_session))

    assert result.rewards == []
    fake_session.commit.assert_not_called()


async def test_replay_gate_skips_expedition_already_credited(
    fake_session: AsyncMock,
) -> None:
    """A re-dispatch of an observation that already completed a step in
    this expedition must not advance it again -- no chaining one
    observation through multiple steps."""
    body = _expedition_body(
        exp_id="x",
        steps=[_step("first"), _step("second")],
    )
    progress = _progress(
        "x",
        completed={
            "first": {
                "completed_at": "2026-05-10T11:00:00+00:00",
                "observation_id": _OBS_ID,
            },
        },
    )
    _wire_session(fake_session, progress_pairs=[(progress, _content("x", body))])

    handler = ExpeditionHandler()
    result = await handler.handle(_ctx(fake_session))

    assert result.rewards == []
    assert "second" not in progress.completed_steps
    fake_session.commit.assert_not_called()


async def test_legacy_string_rows_still_advance(fake_session: AsyncMock) -> None:
    """Rows written before the dict value format hold plain iso strings.
    The gate must not trip on them (no observation_id recorded) and the
    next step still advances with the new format."""
    body = _expedition_body(
        exp_id="x",
        steps=[_step("first"), _step("second"), _step("third")],
    )
    progress = _progress("x", completed={"first": "2026-05-10T11:00:00+00:00"})
    _wire_session(fake_session, progress_pairs=[(progress, _content("x", body))])

    handler = ExpeditionHandler()
    result = await handler.handle(_ctx(fake_session))

    assert [r.type for r in result.rewards] == ["expedition_step"]
    assert result.rewards[0].payload["step_id"] == "second"
    # Legacy value preserved verbatim; the new step uses the dict format.
    assert progress.completed_steps["first"] == "2026-05-10T11:00:00+00:00"
    assert progress.completed_steps["second"] == {
        "completed_at": "2026-05-10T12:00:00+00:00",
        "observation_id": _OBS_ID,
    }
    fake_session.commit.assert_awaited_once()


async def test_ancestor_ids_flow_into_descendant_taxon_match(
    fake_session: AsyncMock,
) -> None:
    """Step wants taxon 3 with descendants; the observation's taxon 12345
    lists 3 in the ancestor chain of its cached iNat payload."""
    body = _expedition_body(
        exp_id="x",
        steps=[_step("bird", "taxon_id", value=3, include_descendants=True)],
    )
    progress = _progress("x")
    species = models.SpeciesCache(
        taxon_id=12345,
        scientific_name="Cardinalis cardinalis",
        common_name="Northern Cardinal",
        iconic_taxon="Aves",
        source_payload={"ancestor_ids": [1, 2, 3, 12345]},
    )
    _wire_session(
        fake_session,
        progress_pairs=[(progress, _content("x", body))],
        species=species,
    )

    handler = ExpeditionHandler()
    result = await handler.handle(_ctx(fake_session))

    types = [r.type for r in result.rewards]
    assert types == ["expedition_step", "expedition_complete"]
    assert progress.completed_steps["bird"]["observation_id"] == _OBS_ID
    fake_session.commit.assert_awaited_once()
