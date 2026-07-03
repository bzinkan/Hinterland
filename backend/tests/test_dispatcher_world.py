"""Unit tests for ``WorldHandler``.

Mirrors the AsyncMock-spec pattern used by ``test_dispatcher_dex.py`` and
``test_dispatcher_rarity.py`` -- no real database, no async event loop
beyond what pytest-asyncio provides, and the planner runs against the
real content tree from ``content/sanctuary/``.

The handler's call sequence (verified by the test wire helpers below):

  1. SELECT species_cache.iconic_taxon                         -- one execute
  2. SELECT sanctuary_zone_state WHERE user_id = ...           -- one execute
  3. SELECT sanctuary_elements   WHERE user_id = ...           -- one execute
  4. INSERT sanctuary_observation_contributions                -- one execute
  5. (if not replay) UPSERT sanctuary_zone_state               -- one execute
  6. (if not replay) INSERT sanctuary_elements (per element)   -- N executes
  7. (if not replay) INSERT sanctuary_events     (per event)   -- M executes

Tests construct the side-effect list explicitly so each call's return
value is verifiable.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import models
from app.dispatcher.handlers.dex import DexHandler
from app.dispatcher.handlers.world import WorldHandler
from app.dispatcher.types import Context, HandlerResult
from app.sanctuary.content import reset_sanctuary_content_cache

_USER_ID = "01J0KIDID0000000000000ULID"
_GROUP_ID = "01J0GROUPID00000000000ULID"
_OBS_ID = "01J0OBSID0000000000000ULID"
_PHOTO_ID = "01J0PHOTOID00000000000ULID"

_MONARCH_TAXON_ID = 48662
_MEADOW_COARSE_PLANTAE = "meadow_coarse_plantae"
_MEADOW_CHARISMATIC_MONARCH = "meadow_charismatic_monarch"


@pytest.fixture(autouse=True)
def _reset_content_cache() -> Iterator[None]:
    reset_sanctuary_content_cache()
    yield
    reset_sanctuary_content_cache()


def _user() -> models.User:
    return models.User(id=_USER_ID, firebase_uid="fb-1", role="kid", display_name="Kid")


def _group() -> models.Group:
    return models.Group(id=_GROUP_ID, name="Family", join_code="ABC123", owner_user_id=_USER_ID)


def _obs(
    *, taxon_id: int | None = 12345, species_name: str | None = "A species"
) -> models.Observation:
    obs = models.Observation(
        id=_OBS_ID,
        user_id=_USER_ID,
        group_id=_GROUP_ID,
        photo_id=_PHOTO_ID,
        latitude=39.1,
        longitude=-84.5,
        taxon_id=taxon_id,
        species_name=species_name,
    )
    obs.created_at = datetime(2026, 6, 3, 12, 0, 0, tzinfo=UTC)
    return obs


@pytest.fixture
def fake_session() -> AsyncMock:
    return AsyncMock(spec=AsyncSession)


def _ctx(
    fake_session: AsyncMock,
    *,
    taxon_id: int | None = 12345,
    species_name: str | None = "A species",
    is_first_find: bool | None = True,
    rarity_tier: str | None = None,
) -> Context:
    obs = _obs(taxon_id=taxon_id, species_name=species_name)
    ctx = Context(
        db=fake_session,
        user=_user(),
        group=_group(),
        observation=obs,
        photo=models.Photo(
            id=_PHOTO_ID,
            user_id=_USER_ID,
            bucket="b",
            object_name=f"pending/{_PHOTO_ID}.jpg",
            status="pending",
        ),
    )
    if is_first_find is not None:
        ctx.results["dex"] = HandlerResult(
            rewards=[],
            state={DexHandler.STATE_IS_FIRST_FIND: is_first_find},
        )
    if rarity_tier is not None:
        ctx.results["rarity"] = HandlerResult(
            rewards=[],
            state={"tier": rarity_tier},
        )
    return ctx


def _scalar(value: object | None) -> MagicMock:
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=value)
    return result


def _scalars_list(rows: list[object]) -> MagicMock:
    """Build a mock that responds to ``.scalars().all()`` with the rows."""
    result = MagicMock()
    scalars = MagicMock()
    scalars.all = MagicMock(return_value=rows)
    result.scalars = MagicMock(return_value=scalars)
    return result


def _wire_session(
    fake_session: AsyncMock,
    *,
    iconic_taxon: str | None,
    zone_rows: list[object] | None = None,
    element_rows: list[object] | None = None,
    contribution_id: str | None = "obs-1",
    element_inserts: list[str | None] | None = None,
    extra_executes: int = 0,
) -> None:
    """Stub session.execute() to walk the handler's call sequence.

    ``element_inserts`` is the per-element INSERT return value (the
    element_id if the row inserted, ``None`` on conflict). Defaults to
    matching ``inserted`` for every element so all rewards/events fire.
    """
    side_effects: list[MagicMock] = []
    side_effects.append(_scalar(iconic_taxon))
    side_effects.append(_scalars_list(zone_rows or []))
    side_effects.append(_scalars_list(element_rows or []))
    side_effects.append(_scalar(contribution_id))
    if contribution_id is not None:
        # UPSERT zone state (one execute; no return value the handler reads).
        side_effects.append(MagicMock())
        # Per-element insert results.
        if element_inserts is not None:
            for inserted in element_inserts:
                side_effects.append(_scalar(inserted))
        # Append events (MagicMock per execute; handler doesn't read).
        for _ in range(extra_executes):
            side_effects.append(MagicMock())

    fake_session.execute = AsyncMock(side_effect=side_effects)
    fake_session.commit = AsyncMock()


# ---------------------------------------------------------------------------
# 1. First observation creates contribution + zone state + coarse element +
#    event + world_unlock reward.
# ---------------------------------------------------------------------------


async def test_first_observation_creates_contribution_zone_state_and_unlock(
    fake_session: AsyncMock,
) -> None:
    _wire_session(
        fake_session,
        iconic_taxon="Plantae",
        contribution_id=_OBS_ID,
        # Exactly one element (the meadow coarse Plantae unlock) +
        # one event (world_unlock for the wake-up).
        element_inserts=[_MEADOW_COARSE_PLANTAE],
        extra_executes=1,
    )
    handler = WorldHandler()
    ctx = _ctx(fake_session, taxon_id=12345, species_name="A plant", is_first_find=True)

    result = await handler.handle(ctx)

    # Exactly one world_unlock reward at the canonical dispatcher weight.
    assert len(result.rewards) == 1
    reward = result.rewards[0]
    assert reward.type == "world_unlock"
    assert reward.weight == 60
    assert reward.payload["zone"] == "meadow"
    assert reward.payload["element_id"] == _MEADOW_COARSE_PLANTAE

    assert result.state[WorldHandler.STATE_REPLAY] is False
    assert result.state[WorldHandler.STATE_ZONE_ID] == "meadow"
    assert 1 in result.state[WorldHandler.STATE_CROSSED_THRESHOLDS]
    fake_session.commit.assert_awaited()


# ---------------------------------------------------------------------------
# 2. Repeat observation increments count but does not duplicate the element.
# ---------------------------------------------------------------------------


async def test_repeat_observation_in_same_zone_no_duplicate_unlock(
    fake_session: AsyncMock,
) -> None:
    existing_element = MagicMock()
    existing_element.user_id = _USER_ID
    existing_element.zone_id = "meadow"
    existing_element.element_id = _MEADOW_COARSE_PLANTAE
    existing_element.element_type = "coarse"
    existing_zone = MagicMock()
    existing_zone.user_id = _USER_ID
    existing_zone.zone_id = "meadow"
    existing_zone.observation_count = 1
    existing_zone.depth_tier = 1

    _wire_session(
        fake_session,
        iconic_taxon="Plantae",
        zone_rows=[existing_zone],
        element_rows=[existing_element],
        contribution_id=_OBS_ID,
        # is_first_find=False -> planner generates NO elements at all;
        # no per-element INSERTs, no events for 1->2 (not a threshold).
        element_inserts=[],
        extra_executes=0,
    )
    handler = WorldHandler()
    ctx = _ctx(fake_session, taxon_id=12345, species_name="A plant", is_first_find=False)

    result = await handler.handle(ctx)

    # No new rewards (no world_unlock, no world_evolution -- not crossing
    # a threshold). Zone state still upserted (count 1 -> 2).
    assert result.rewards == []
    assert result.state[WorldHandler.STATE_BEFORE_TIER] == 1
    assert result.state[WorldHandler.STATE_CROSSED_THRESHOLDS] == []


# ---------------------------------------------------------------------------
# 3. Crossing a threshold emits world_evolution.
# ---------------------------------------------------------------------------


async def test_crossing_threshold_emits_world_evolution(
    fake_session: AsyncMock,
) -> None:
    existing_element = MagicMock()
    existing_element.user_id = _USER_ID
    existing_element.zone_id = "meadow"
    existing_element.element_id = _MEADOW_COARSE_PLANTAE
    existing_element.element_type = "coarse"
    existing_zone = MagicMock()
    existing_zone.user_id = _USER_ID
    existing_zone.zone_id = "meadow"
    existing_zone.observation_count = 2  # going to 3 -> threshold crossed
    existing_zone.depth_tier = 1

    # Going from 2 -> 3 crosses threshold 3. The planner emits:
    # - the meadow tiny-surprise element (one INSERT) +
    # - a world_evolution reward (from the tiny surprise) +
    # - the threshold-3 world_evolution reward + event.
    _wire_session(
        fake_session,
        iconic_taxon="Plantae",
        zone_rows=[existing_zone],
        element_rows=[existing_element],
        contribution_id=_OBS_ID,
        element_inserts=["meadow_surprise_drifting_petal"],
        extra_executes=3,  # generous: tiny-surprise event + threshold event + buffer
    )
    handler = WorldHandler()
    ctx = _ctx(fake_session, taxon_id=12345, species_name="A plant", is_first_find=False)

    result = await handler.handle(ctx)

    evolution = [r for r in result.rewards if r.type == "world_evolution"]
    assert evolution, "expected at least one world_evolution reward"
    assert all(r.weight == 30 for r in evolution)
    assert 3 in result.state[WorldHandler.STATE_CROSSED_THRESHOLDS]


# ---------------------------------------------------------------------------
# 4. Replay: same observation dispatched twice does not double-count.
# ---------------------------------------------------------------------------


async def test_replay_same_observation_does_not_double_count(
    fake_session: AsyncMock,
) -> None:
    # The contribution INSERT on the second dispatch returns None
    # (ON CONFLICT DO NOTHING) -- the handler short-circuits with NO
    # subsequent writes.
    _wire_session(
        fake_session,
        iconic_taxon="Plantae",
        contribution_id=None,  # replay
        element_inserts=None,
        extra_executes=0,
    )
    handler = WorldHandler()
    ctx = _ctx(fake_session, taxon_id=12345, species_name="A plant", is_first_find=True)

    result = await handler.handle(ctx)

    assert result.rewards == []
    assert result.state[WorldHandler.STATE_REPLAY] is True
    assert result.state[WorldHandler.STATE_CONTRIBUTION_ID] is None

    # The session saw EXACTLY 4 executes: species_cache, zone_states,
    # elements, contribution INSERT. NO zone_state upsert, NO element
    # inserts, NO events.
    assert fake_session.execute.await_count == 4


# ---------------------------------------------------------------------------
# 5. First charismatic taxon unlocks the charismatic element.
# ---------------------------------------------------------------------------


async def test_charismatic_unlock_when_taxon_id_matches(
    fake_session: AsyncMock,
) -> None:
    # First-find monarch (taxon_id 48662) -- the planner emits:
    # - meadow_coarse_insecta (zone wake-up, weight 60) +
    # - meadow_charismatic_monarch (charismatic, weight 60) +
    # - meadow_pollination_moment NOT here (user has no Plantae yet).
    _wire_session(
        fake_session,
        iconic_taxon="Insecta",
        contribution_id=_OBS_ID,
        element_inserts=["meadow_coarse_insecta", _MEADOW_CHARISMATIC_MONARCH],
        extra_executes=2,  # one event per element
    )
    handler = WorldHandler()
    ctx = _ctx(
        fake_session,
        taxon_id=_MONARCH_TAXON_ID,
        species_name="Monarch butterfly",
        is_first_find=True,
    )

    result = await handler.handle(ctx)

    charismatic_rewards = [
        r for r in result.rewards if r.payload.get("unlock_kind") == "charismatic"
    ]
    assert len(charismatic_rewards) == 1
    assert charismatic_rewards[0].type == "world_unlock"
    assert charismatic_rewards[0].weight == 60
    assert charismatic_rewards[0].payload["element_id"] == _MEADOW_CHARISMATIC_MONARCH
    assert charismatic_rewards[0].payload["taxon_id"] == _MONARCH_TAXON_ID


# ---------------------------------------------------------------------------
# 6. Missing SpeciesCache routes to elsewhere but still contributes.
# ---------------------------------------------------------------------------


async def test_missing_species_cache_maps_to_elsewhere(
    fake_session: AsyncMock,
) -> None:
    _wire_session(
        fake_session,
        iconic_taxon=None,  # species_cache row absent / NULL
        contribution_id=_OBS_ID,
        # No coarse unlock (iconic_taxon=None per the planner's rule);
        # zone state still gets bumped for elsewhere.
        element_inserts=[],
        extra_executes=0,
    )
    handler = WorldHandler()
    ctx = _ctx(fake_session, taxon_id=99999, species_name="Mystery", is_first_find=True)

    result = await handler.handle(ctx)

    assert result.state[WorldHandler.STATE_ZONE_ID] == "elsewhere"
    assert result.state[WorldHandler.STATE_CONTRIBUTION_ID] == _OBS_ID
    # Threshold 1 IS crossed but no coarse unlock owns the "wake-up"
    # since iconic_taxon=None -- so no world_unlock reward and no
    # world_evolution either (threshold 1 is the wake-up, skipped).
    assert all(r.type != "world_unlock" for r in result.rewards)


# ---------------------------------------------------------------------------
# 7. Handler does NOT touch memberships.
# ---------------------------------------------------------------------------


async def test_handler_does_not_touch_memberships(fake_session: AsyncMock) -> None:
    _wire_session(
        fake_session,
        iconic_taxon="Plantae",
        contribution_id=_OBS_ID,
        element_inserts=[_MEADOW_COARSE_PLANTAE],
        extra_executes=1,
    )
    handler = WorldHandler()
    ctx = _ctx(fake_session, taxon_id=12345, species_name="A plant", is_first_find=True)

    await handler.handle(ctx)

    # Every execute call was either a SELECT or an INSERT/UPDATE on a
    # `sanctuary_*` table. The handler must never reference the
    # memberships table.
    for call in fake_session.execute.await_args_list:
        stmt = call.args[0]
        # SQLAlchemy compiles to a string lazily; force it and grep.
        rendered = str(stmt).lower()
        assert "memberships" not in rendered, (
            f"WorldHandler must not touch memberships; "
            f"saw statement against memberships: {rendered}"
        )


# ---------------------------------------------------------------------------
# 8. Internal exception returns empty HandlerResult; does not raise.
# ---------------------------------------------------------------------------


async def test_handler_failure_returns_empty_result_not_raise(
    fake_session: AsyncMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failure inside the handler must NOT escape to the dispatcher --
    submission must not depend on Sanctuary success (AGENTS.md)."""

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("simulated planner failure")

    import app.dispatcher.handlers.world as world_module

    monkeypatch.setattr(world_module, "compute_sanctuary_plan", _boom)

    fake_session.execute = AsyncMock(
        side_effect=[_scalar("Plantae"), _scalars_list([]), _scalars_list([])]
    )
    fake_session.commit = AsyncMock()

    handler = WorldHandler()
    ctx = _ctx(fake_session, taxon_id=12345, species_name="A plant", is_first_find=True)

    result = await handler.handle(ctx)

    assert result.rewards == []
    assert result.state.get(WorldHandler.STATE_ERROR) is True


# ---------------------------------------------------------------------------
# 9. Taxonless observation skips entirely -- no DB touch, no contribution.
#    Sanctuary participation begins at identification (2026-07-03).
# ---------------------------------------------------------------------------


async def test_taxonless_observation_skips_without_touching_db(
    fake_session: AsyncMock,
) -> None:
    """The live mobile flow creates observations with no taxon; the old
    behavior claimed the per-observation replay gate with zone
    'elsewhere', permanently blocking the taxon-time re-dispatch."""
    fake_session.execute = AsyncMock()
    fake_session.commit = AsyncMock()

    handler = WorldHandler()
    ctx = _ctx(fake_session, taxon_id=None, species_name=None, is_first_find=False)

    result = await handler.handle(ctx)

    assert result.rewards == []
    assert result.state[WorldHandler.STATE_SKIPPED_NO_TAXON] is True
    assert result.state[WorldHandler.STATE_REPLAY] is False
    assert result.state[WorldHandler.STATE_CONTRIBUTION_ID] is None
    # No reads, no writes, no commit: the observation left no Sanctuary
    # footprint to collide with later.
    fake_session.execute.assert_not_awaited()
    fake_session.commit.assert_not_awaited()


# ---------------------------------------------------------------------------
# 10. The taxon-time re-dispatch after a taxonless create contributes
#     fresh -- the full unlock fires because no row was claimed at create.
# ---------------------------------------------------------------------------


async def test_redispatch_after_taxonless_create_contributes_fresh(
    fake_session: AsyncMock,
) -> None:
    handler = WorldHandler()

    # Create-time dispatch: taxonless -> skip, zero DB calls.
    create_ctx = _ctx(fake_session, taxon_id=None, species_name=None, is_first_find=False)
    fake_session.execute = AsyncMock()
    fake_session.commit = AsyncMock()
    create_result = await handler.handle(create_ctx)
    assert create_result.state[WorldHandler.STATE_SKIPPED_NO_TAXON] is True
    fake_session.execute.assert_not_awaited()

    # Taxon-time re-dispatch: same observation id, taxon now present and
    # DexHandler just minted the first find. The contribution INSERT
    # succeeds (no create-time row to conflict with) and the coarse
    # unlock fires exactly as for a created-with-taxon observation.
    _wire_session(
        fake_session,
        iconic_taxon="Plantae",
        contribution_id=_OBS_ID,
        element_inserts=[_MEADOW_COARSE_PLANTAE],
        extra_executes=1,
    )
    redispatch_ctx = _ctx(fake_session, taxon_id=12345, species_name="A plant", is_first_find=True)

    result = await handler.handle(redispatch_ctx)

    assert len(result.rewards) == 1
    assert result.rewards[0].type == "world_unlock"
    assert result.rewards[0].payload["zone"] == "meadow"
    assert result.state[WorldHandler.STATE_REPLAY] is False
    assert result.state.get(WorldHandler.STATE_SKIPPED_NO_TAXON) is None
