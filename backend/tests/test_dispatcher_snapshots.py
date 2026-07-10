"""Snapshot scenarios from `docs/dispatcher.md` table.

Each test maps to a numbered scenario in the doc. The tests stub the
SQLAlchemy session rather than spin up real Postgres -- that's a Phase 11
follow-up for the beta-polish run-the-real-DB harness. The shape we lock
in here is the *contract* between handlers and the dispatcher: given
this DB state, this is the exact ordered list of rewards the kid sees.

Scenarios covered:
  1. First find, common species, common region
  2. Repeat find, common region
  3. First find, rare species
  4. First find, low_data region -> geohash-3 fallback
  5. Unrecorded species
  6. Observation completes an expedition step
  7. Observation completes the final expedition step
  8. One observation advances two expeditions
  9. One handler raises; others still run

Scenarios 10 (idempotent re-submit) and 11 (replay after crash) need a
real DB to be meaningful; their handler-level invariants (Dex's
INSERT ... ON CONFLICT, Rarity's idempotent UPSERT) are exercised by
the per-handler unit tests.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import models
from app.dispatcher.core import dispatch
from app.dispatcher.handlers.dex import DexHandler
from app.dispatcher.handlers.expedition import ExpeditionHandler
from app.dispatcher.handlers.rarity import RarityHandler
from app.dispatcher.types import Context, Handler, HandlerResult

_USER_ID = "01J0KIDID0000000000000ULID"
_GROUP_ID = "01J0GROUPID00000000000ULID"
_OBS_ID = "01J0OBSID0000000000000ULID"
_PHOTO_ID = "01J0PHOTOID00000000000ULID"


def _user() -> models.User:
    return models.User(id=_USER_ID, firebase_uid="fb-1", role="kid", display_name="Kid")


def _group() -> models.Group:
    return models.Group(id=_GROUP_ID, name="Family", join_code="ABC123", owner_user_id=_USER_ID)


def _obs(*, taxon_id: int = 12345, species_name: str = "Northern Cardinal") -> models.Observation:
    obs = models.Observation(
        id=_OBS_ID,
        user_id=_USER_ID,
        group_id=_GROUP_ID,
        photo_id=_PHOTO_ID,
        latitude=39.1,
        longitude=-84.5,
        taxon_id=taxon_id,
        species_name=species_name,
        geohash4="dnp1",
    )
    obs.created_at = datetime(2026, 5, 10, 12, 0, 0, tzinfo=UTC)
    return obs


def _ctx(fake_session: AsyncMock) -> Context:
    return Context(
        db=fake_session,
        user=_user(),
        group=_group(),
        observation=_obs(),
        photo=models.Photo(
            id=_PHOTO_ID,
            user_id=_USER_ID,
            bucket="b",
            object_name=f"observations/{_PHOTO_ID}.jpg",
            status="clean",
        ),
    )


def _wire_for_dex_first_then_rarity(
    fake_session: AsyncMock,
    *,
    dex_inserted_id: str | None,
    rarity_species_row: models.RarityCache | None,
    region_seen: bool = True,
) -> None:
    """Wire `execute()` for the dispatcher run.

    Order of execute() calls (dex then rarity then expedition stub):
    1. DexHandler INSERT ... ON CONFLICT RETURNING
    2. (first-find only) DexHandler counter UPDATE
    3. RarityHandler species lookup
    4. (rarity miss only) RarityHandler region-existence lookup
    5. RarityHandler rarest_tier UPDATE (when observed_tier set)

    ExpeditionHandler stub returns empty without touching the session.
    """
    dex_insert_result = MagicMock()
    dex_insert_result.scalar_one_or_none = MagicMock(return_value=dex_inserted_id)

    dex_update_result = MagicMock()

    rarity_species_result = MagicMock()
    rarity_species_result.scalar_one_or_none = MagicMock(return_value=rarity_species_row)

    rarity_region_result = MagicMock()
    rarity_region_result.scalar_one_or_none = MagicMock(
        return_value="dnp1" if region_seen else None
    )

    rarity_update_result = MagicMock()

    side_effects: list[Any] = [dex_insert_result]
    is_first_find = dex_inserted_id is not None
    if is_first_find:
        side_effects.append(dex_update_result)
    side_effects.append(rarity_species_result)
    if rarity_species_row is None:
        side_effects.append(rarity_region_result)
    # rarest_tier UPDATE -- skipped only when both: rarity hit was None
    # AND region cold-start (observed_tier stays None).
    if rarity_species_row is not None or region_seen:
        side_effects.append(rarity_update_result)

    fake_session.execute = AsyncMock(side_effect=side_effects)
    fake_session.commit = AsyncMock()


def _rarity_row(tier: str) -> models.RarityCache:
    return models.RarityCache(
        id="dnp1:12345",
        region_geohash="dnp1",
        taxon_id=12345,
        tier=tier,
        observation_count=1,
        refreshed_at=datetime(2026, 5, 9, 3, 0, 0, tzinfo=UTC),
    )


@pytest.fixture
def fake_session() -> AsyncMock:
    return AsyncMock(spec=AsyncSession)


_HANDLERS: list[Handler] = [
    DexHandler(),
    RarityHandler(),
    ExpeditionHandler(),
]


# ---------------------------------------------------------------------------
# Scenario 1: First observation ever, common species, common region.
# Expected: first_find (80) + rarity_tier common (10), in that weight order.
# ---------------------------------------------------------------------------


async def test_scenario_1_first_find_common_species_common_region(
    fake_session: AsyncMock,
) -> None:
    _wire_for_dex_first_then_rarity(
        fake_session,
        dex_inserted_id="dex-row-1",
        rarity_species_row=_rarity_row("common"),
    )
    rewards = await dispatch(_ctx(fake_session), _HANDLERS)
    assert [r.type for r in rewards] == ["first_find", "rarity_tier"]
    assert [r.weight for r in rewards] == [80, 10]


# ---------------------------------------------------------------------------
# Scenario 2: Repeat find, same species as #1 (still common region).
# Expected: rarity_tier common (10), repeat_find (10) -- equal weights resolve
# by handler registration order (dex then rarity).
# ---------------------------------------------------------------------------


async def test_scenario_2_repeat_find_common_region(fake_session: AsyncMock) -> None:
    _wire_for_dex_first_then_rarity(
        fake_session,
        dex_inserted_id=None,  # ON CONFLICT fired -> repeat
        rarity_species_row=_rarity_row("common"),
    )
    rewards = await dispatch(_ctx(fake_session), _HANDLERS)
    assert [r.type for r in rewards] == ["repeat_find", "rarity_tier"]
    assert [r.weight for r in rewards] == [10, 10]


# ---------------------------------------------------------------------------
# Scenario 3: First find, rare species, common region.
# Expected: first_find (80), rarity_tier rare (40).
# ---------------------------------------------------------------------------


async def test_scenario_3_first_find_rare_species(fake_session: AsyncMock) -> None:
    _wire_for_dex_first_then_rarity(
        fake_session,
        dex_inserted_id="dex-row-1",
        rarity_species_row=_rarity_row("rare"),
    )
    rewards = await dispatch(_ctx(fake_session), _HANDLERS)
    assert [r.type for r in rewards] == ["first_find", "rarity_tier"]
    assert [r.weight for r in rewards] == [80, 40]


# ---------------------------------------------------------------------------
# Scenario 5: Unrecorded species (never seen in region before).
# Expected: unrecorded (100), first_find (80).
# ---------------------------------------------------------------------------


async def test_scenario_5_unrecorded_species(fake_session: AsyncMock) -> None:
    _wire_for_dex_first_then_rarity(
        fake_session,
        dex_inserted_id="dex-row-1",
        rarity_species_row=None,
        region_seen=True,
    )
    rewards = await dispatch(_ctx(fake_session), _HANDLERS)
    assert [r.type for r in rewards] == ["unrecorded", "first_find"]
    assert [r.weight for r in rewards] == [100, 80]


# ---------------------------------------------------------------------------
# Scenario 9: One handler raises; others still produce rewards.
# Expected: dispatcher catches the exception, records no fabricated result,
# and independent handlers' rewards still flow.
# ---------------------------------------------------------------------------


class _BoomMidHandler:
    name = "boom"

    async def handle(self, ctx: Context) -> HandlerResult:
        raise RuntimeError("intentional")


async def test_scenario_9_handler_raises_others_still_run(
    fake_session: AsyncMock,
) -> None:
    _wire_for_dex_first_then_rarity(
        fake_session,
        dex_inserted_id="dex-row-1",
        rarity_species_row=_rarity_row("common"),
    )
    handlers: list[Handler] = [
        DexHandler(),
        _BoomMidHandler(),
        RarityHandler(),
        ExpeditionHandler(),
    ]
    ctx = _ctx(fake_session)
    rewards = await dispatch(ctx, handlers)

    # Boom handler produced nothing; the other two still emit their rewards.
    assert {r.type for r in rewards} == {"first_find", "rarity_tier"}
    # A failed handler must not masquerade as a successful predecessor.
    assert "boom" not in ctx.results


# ---------------------------------------------------------------------------
# Scenario 4: First find, geohash-4 cold-start, geohash-3 parent has the species.
# Expected: first_find (80), rarity_tier (from the geohash-3 fallback).
# See `app.dispatcher.handlers.rarity._lookup_rarity`: when the direct
# region returns cold_start AND a 3-char parent exists, the handler
# queries the parent and uses that tier instead.
# ---------------------------------------------------------------------------


def _wire_scenario_4(fake_session: AsyncMock) -> None:
    """Dex first-find + Rarity geohash-3 fallback path.

    Execute call order (rarity does two lookups per region):
      1. Dex INSERT ON CONFLICT -> returns 'dex-row-1' (first find)
      2. Dex UPDATE counter
      3. Rarity geohash-4 species lookup -> None
      4. Rarity geohash-4 region-existence lookup -> None (cold_start)
      5. Rarity geohash-3 species lookup -> rarity_row (parent has species)
      6. (no region-existence -- species hit short-circuits)
      7. Rarity rarest_tier UPDATE
    """
    dex_insert = MagicMock()
    dex_insert.scalar_one_or_none = MagicMock(return_value="dex-row-1")
    dex_update = MagicMock()

    rarity_species_dnp1 = MagicMock()
    rarity_species_dnp1.scalar_one_or_none = MagicMock(return_value=None)
    rarity_region_dnp1 = MagicMock()
    rarity_region_dnp1.scalar_one_or_none = MagicMock(return_value=None)

    rarity_species_dnp = MagicMock()
    rarity_species_dnp.scalar_one_or_none = MagicMock(return_value=_rarity_row("rare"))

    rarity_update = MagicMock()

    fake_session.execute = AsyncMock(
        side_effect=[
            dex_insert,
            dex_update,
            rarity_species_dnp1,
            rarity_region_dnp1,
            rarity_species_dnp,
            rarity_update,
        ]
    )
    fake_session.commit = AsyncMock()


async def test_scenario_4_low_data_fallback_to_geohash_3(
    fake_session: AsyncMock,
) -> None:
    _wire_scenario_4(fake_session)
    # Only run Dex + Rarity (skip ExpeditionHandler so we don't have to
    # wire its empty-progress lookup; the expedition behavior is the
    # subject of scenarios 6/7/8).
    handlers: list[Handler] = [DexHandler(), RarityHandler()]
    rewards = await dispatch(_ctx(fake_session), handlers)
    assert [r.type for r in rewards] == ["first_find", "rarity_tier"]
    # rarity_tier is "rare" -> weight 40 per docs/dispatcher.md.
    assert [r.weight for r in rewards] == [80, 40]


# ---------------------------------------------------------------------------
# Expedition scenarios 6 / 7 / 8.
# ---------------------------------------------------------------------------


def _exp_content(
    *,
    exp_id: str,
    title: str,
    steps: list[tuple[str, str]],
) -> models.ExpeditionContent:
    """Build an ExpeditionContent whose body is a valid Expedition with
    N steps that use ``{"kind": "any_organism"}`` -- which matches every
    observation. Caller controls how many steps are advanced via the
    ``completed_steps`` field on the matching ExpeditionProgress row.
    """
    body = {
        "id": exp_id,
        "title": title,
        "subtitle": "test",
        "tier": 1,
        "duration_minutes": 20,
        "environments": ["other"],
        "intro": "test",
        "outro": "well done",
        "prerequisites": [],
        "steps": [
            {
                "id": step_id,
                "description": step_desc,
                "match": {"kind": "any_organism"},
                "hint": "look",
            }
            for step_id, step_desc in steps
        ],
    }
    return models.ExpeditionContent(
        id=exp_id,
        tier=1,
        content_hash=f"hash-{exp_id}",
        body=body,
        archived=False,
    )


def _exp_progress(
    *,
    exp_id: str,
    completed_step_ids: list[str] | None = None,
) -> models.ExpeditionProgress:
    progress = models.ExpeditionProgress(
        id=f"prog-{exp_id}",
        user_id=_USER_ID,
        group_id=_GROUP_ID,
        expedition_id=exp_id,
        completed_steps=dict.fromkeys(
            completed_step_ids or [],
            "2026-05-09T12:00:00+00:00",
        ),
        completed_at=None,
    )
    progress.created_at = datetime(2026, 5, 10, 10, 0, 0, tzinfo=UTC)
    return progress


def _wire_expedition_full_dispatch(
    fake_session: AsyncMock,
    *,
    progress_pairs: list[tuple[models.ExpeditionProgress, models.ExpeditionContent]],
) -> None:
    """Wire the full Dex + Rarity + Expedition flow.

    Execute call order:
      1. Dex INSERT ON CONFLICT -> first-find ID
      2. Dex UPDATE counter
      3. Rarity species lookup -> common-tier row (so rarity_tier fires)
      4. Rarity rarest_tier UPDATE
      5. Expedition: SELECT (progress, content) JOIN
      6. Expedition: species_cache lookup (taxon_id is set)
      7. Expedition: dex_rows lookup
      8. Expedition: prior_rows lookup -- only consumed when an active
         step uses the radius matcher; these scenarios are all
         any_organism, so it stays queued (harmless leftover)
    """
    dex_insert = MagicMock()
    dex_insert.scalar_one_or_none = MagicMock(return_value="dex-row-1")
    dex_update = MagicMock()

    rarity_species = MagicMock()
    rarity_species.scalar_one_or_none = MagicMock(return_value=_rarity_row("common"))
    rarity_update = MagicMock()

    exp_progress_result = MagicMock()
    exp_progress_result.all = MagicMock(return_value=progress_pairs)

    species_cache = MagicMock()
    species_cache.scalar_one_or_none = MagicMock(return_value=None)

    dex_rows = MagicMock()
    dex_rows.all = MagicMock(return_value=[])

    prior_rows = MagicMock()
    prior_rows.all = MagicMock(return_value=[])

    contribution_results: list[MagicMock] = []
    for _progress_row, _content_row in progress_pairs:
        result = MagicMock()
        result.scalar_one_or_none = MagicMock(return_value=_OBS_ID)
        contribution_results.append(result)

    fake_session.execute = AsyncMock(
        side_effect=[
            dex_insert,
            dex_update,
            rarity_species,
            rarity_update,
            exp_progress_result,
            species_cache,
            dex_rows,
            prior_rows,
            *contribution_results,
        ]
    )
    fake_session.commit = AsyncMock()


async def test_scenario_6_observation_completes_an_expedition_step(
    fake_session: AsyncMock,
) -> None:
    """Active expedition with 3 steps, none completed. The observation
    matches the first incomplete step (`any_organism`). Expect a single
    `expedition_step` reward; no `expedition_complete` yet."""
    content = _exp_content(
        exp_id="backyard_starter",
        title="Backyard Starter",
        steps=[("first", "any organism"), ("second", "any organism"), ("third", "any organism")],
    )
    progress = _exp_progress(exp_id="backyard_starter")
    _wire_expedition_full_dispatch(fake_session, progress_pairs=[(progress, content)])

    rewards = await dispatch(_ctx(fake_session), _HANDLERS)
    assert [r.type for r in rewards] == ["first_find", "expedition_step", "rarity_tier"]
    assert [r.weight for r in rewards] == [80, 40, 10]
    # The advanced step is the first one.
    exp_step = next(r for r in rewards if r.type == "expedition_step")
    assert exp_step.payload["expedition_id"] == "backyard_starter"
    assert exp_step.payload["step_id"] == "first"
    # Progress mutated in place; first step is now in completed_steps,
    # recorded with the iso timestamp + crediting observation.
    assert progress.completed_steps["first"] == {
        "completed_at": "2026-05-10T12:00:00+00:00",
        "observation_id": _OBS_ID,
    }


async def test_scenario_7_observation_completes_the_final_step(
    fake_session: AsyncMock,
) -> None:
    """Active expedition with 3 steps; the first two are already
    completed. The observation matches the third step. Expect both an
    `expedition_step` AND an `expedition_complete` reward -- with the
    completion (60) sorting BEFORE its own step (40) in the celebration."""
    content = _exp_content(
        exp_id="park_starter",
        title="Park Starter",
        steps=[("one", "any organism"), ("two", "any organism"), ("three", "any organism")],
    )
    progress = _exp_progress(exp_id="park_starter", completed_step_ids=["one", "two"])
    _wire_expedition_full_dispatch(fake_session, progress_pairs=[(progress, content)])

    rewards = await dispatch(_ctx(fake_session), _HANDLERS)
    reward_types = [r.type for r in rewards]
    assert reward_types == ["first_find", "expedition_complete", "expedition_step", "rarity_tier"]
    assert [r.weight for r in rewards] == [80, 60, 40, 10]
    complete_reward = next(r for r in rewards if r.type == "expedition_complete")
    assert complete_reward.payload == {"expedition_id": "park_starter"}
    # Progress row got completed_at stamped; the final step is recorded
    # in the dict value format crediting this observation.
    assert progress.completed_at is not None
    assert progress.completed_steps["three"] == {
        "completed_at": "2026-05-10T12:00:00+00:00",
        "observation_id": _OBS_ID,
    }


async def test_scenario_8_one_observation_advances_two_expeditions(
    fake_session: AsyncMock,
) -> None:
    """Two active expeditions, both starting at step 0. The observation
    matches the first step of each. Expect TWO `expedition_step` rewards
    (one per expedition); the docs/dispatcher.md correctness property
    "at most one step per expedition" still holds (each gets exactly
    one)."""
    content_a = _exp_content(
        exp_id="exp_a",
        title="Exp A",
        steps=[("a1", "any organism"), ("a2", "any organism")],
    )
    content_b = _exp_content(
        exp_id="exp_b",
        title="Exp B",
        steps=[("b1", "any organism"), ("b2", "any organism")],
    )
    progress_a = _exp_progress(exp_id="exp_a")
    progress_b = _exp_progress(exp_id="exp_b")

    _wire_expedition_full_dispatch(
        fake_session,
        progress_pairs=[(progress_a, content_a), (progress_b, content_b)],
    )

    rewards = await dispatch(_ctx(fake_session), _HANDLERS)
    step_rewards = [r for r in rewards if r.type == "expedition_step"]
    assert len(step_rewards) == 2
    advanced_ids = {r.payload["expedition_id"] for r in step_rewards}
    assert advanced_ids == {"exp_a", "exp_b"}
    # Each progress row got its first step advanced, both crediting
    # this observation in the dict value format.
    assert progress_a.completed_steps["a1"]["observation_id"] == _OBS_ID
    assert progress_b.completed_steps["b1"]["observation_id"] == _OBS_ID
    # No `expedition_complete` -- neither expedition is done.
    assert not any(r.type == "expedition_complete" for r in rewards)
