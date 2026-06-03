"""Pure-computation unit tests for ``app.sanctuary.service``.

No database, no async, no network. Each test constructs a ``ServiceInputs``
dataclass directly, calls ``compute_sanctuary_plan`` against the REAL
content loaded from ``content/sanctuary/``, and asserts on the resulting
``SanctuaryPlan``.

Reset the content cache between tests in case a future test mutates the
fixture content tree (none currently do, but resetting is cheap).
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date

import pytest

from app.sanctuary.content import get_sanctuary_content, reset_sanctuary_content_cache
from app.sanctuary.service import compute_sanctuary_plan
from app.sanctuary.types import (
    ElementSnapshot,
    ElementToUnlock,
    ObservationInput,
    PlannedEvent,
    Reward,
    SanctuaryPlan,
    ServiceInputs,
    ZoneId,
    ZoneStateSnapshot,
)

_TODAY = date(2026, 6, 3)
_USER_ID = "01J0USER000000000000000001"
_OBSERVATION_ID = "01J0OBSERVATION000000000001"

# Real content ids the tests assert on. These exist in
# content/sanctuary/*.json (PR #96). If an author renames one of these in
# content, the test will surface the drift immediately.
_MEADOW_COARSE_PLANTAE = "meadow_coarse_plantae"
_MEADOW_COARSE_INSECTA = "meadow_coarse_insecta"
_POND_COARSE_AMPHIBIA = "pond_coarse_amphibia"
_MEADOW_CHARISMATIC_MONARCH = "meadow_charismatic_monarch"
_MEADOW_POLLINATION_MOMENT = "meadow_pollination_moment"
_MONARCH_TAXON_ID = 48662


@pytest.fixture(autouse=True)
def _reset_content_cache() -> Iterator[None]:
    reset_sanctuary_content_cache()
    yield
    reset_sanctuary_content_cache()


def _make_observation(
    *,
    taxon_id: int | None = None,
    species_name: str | None = None,
    iconic_taxon: str | None = None,
    is_first_find: bool = True,
) -> ObservationInput:
    return ObservationInput(
        user_id=_USER_ID,
        observation_id=_OBSERVATION_ID,
        taxon_id=taxon_id,
        species_name=species_name,
        iconic_taxon=iconic_taxon,
        is_first_find=is_first_find,
        current_date=_TODAY,
    )


def _zone_state(zone_id: ZoneId, count: int, tier: int = 0) -> ZoneStateSnapshot:
    return ZoneStateSnapshot(
        user_id=_USER_ID,
        zone_id=zone_id,
        observation_count=count,
        depth_tier=tier,
    )


def _existing_element(
    zone_id: ZoneId, element_id: str, element_type: str = "coarse"
) -> ElementSnapshot:
    # element_type is a Literal at the type layer; pass via cast at call site
    # where strict typing matters. For runtime tests the str is fine.
    return ElementSnapshot(
        user_id=_USER_ID,
        zone_id=zone_id,
        element_id=element_id,
        element_type=element_type,  # type: ignore[arg-type]
    )


def _compute(
    *,
    observation: ObservationInput,
    zone_states: list[ZoneStateSnapshot] | None = None,
    elements: list[ElementSnapshot] | None = None,
) -> SanctuaryPlan:
    content = get_sanctuary_content()
    return compute_sanctuary_plan(
        ServiceInputs(
            observation=observation,
            zone_states=zone_states or [],
            elements=elements or [],
        ),
        content,
    )


# ---------------------------------------------------------------------------
# Zone routing tests
# ---------------------------------------------------------------------------


def test_plantae_maps_to_meadow() -> None:
    plan = _compute(observation=_make_observation(iconic_taxon="Plantae"))
    assert plan.contribution_zone_id == "meadow"


def test_insecta_maps_to_meadow() -> None:
    plan = _compute(observation=_make_observation(iconic_taxon="Insecta"))
    assert plan.contribution_zone_id == "meadow"


def test_amphibia_maps_to_pond() -> None:
    plan = _compute(observation=_make_observation(iconic_taxon="Amphibia"))
    assert plan.contribution_zone_id == "pond"


def test_unknown_iconic_taxon_maps_to_elsewhere() -> None:
    """taxon_id set, iconic_taxon=None -> elsewhere."""
    plan = _compute(observation=_make_observation(taxon_id=999_999, iconic_taxon=None))
    assert plan.contribution_zone_id == "elsewhere"


def test_missing_taxon_maps_to_elsewhere() -> None:
    """Neither iconic_taxon nor taxon_id supplied -> elsewhere."""
    plan = _compute(observation=_make_observation(taxon_id=None, iconic_taxon=None))
    assert plan.contribution_zone_id == "elsewhere"


# ---------------------------------------------------------------------------
# Coarse-unlock and idempotency tests
# ---------------------------------------------------------------------------


def test_first_observation_unlocks_zone_and_coarse_element() -> None:
    plan = _compute(observation=_make_observation(iconic_taxon="Plantae"))

    # Zone transition: 0 -> 1, crossed threshold 1.
    [transition] = plan.zone_transitions
    assert transition.zone_id == "meadow"
    assert transition.before_count == 0
    assert transition.after_count == 1
    assert transition.crossed_thresholds == (1,)

    # The coarse element fires exactly once.
    coarse_unlocks = _of_type(plan.elements_to_unlock, "coarse")
    assert len(coarse_unlocks) == 1
    assert coarse_unlocks[0].element_id == _MEADOW_COARSE_PLANTAE

    # One world_unlock reward + one world_unlock event for the wake-up.
    unlock_rewards = _of_reward_type(plan.rewards, "world_unlock")
    assert len(unlock_rewards) == 1
    unlock_events = _of_event_type(plan.events, "world_unlock")
    assert len(unlock_events) == 1
    assert unlock_events[0].element_id == _MEADOW_COARSE_PLANTAE

    # Crossing threshold 1 must NOT also emit a world_evolution event --
    # threshold 1 is the wake-up, owned by the coarse unlock above.
    assert _of_event_type(plan.events, "world_evolution") == []


def test_repeat_observation_increments_depth_no_duplicate_element() -> None:
    """Second Plantae observation -- count goes up, but the existing
    ``meadow_coarse_plantae`` element does NOT fire again."""
    plan = _compute(
        observation=_make_observation(iconic_taxon="Plantae"),
        zone_states=[_zone_state("meadow", count=1, tier=1)],
        elements=[_existing_element("meadow", _MEADOW_COARSE_PLANTAE)],
    )

    [transition] = plan.zone_transitions
    assert transition.before_count == 1
    assert transition.after_count == 2
    # 2 is not in {1, 3, 5, 10, 20, 50}; no thresholds crossed.
    assert transition.crossed_thresholds == ()

    # No duplicate coarse element.
    assert _MEADOW_COARSE_PLANTAE not in {e.element_id for e in plan.elements_to_unlock}
    assert _of_type(plan.elements_to_unlock, "coarse") == []
    # And no world_unlock reward.
    assert _of_reward_type(plan.rewards, "world_unlock") == []


# ---------------------------------------------------------------------------
# Threshold crossing tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("before_count", "expected_threshold"),
    [
        (2, 3),
        (4, 5),
        (9, 10),
        (19, 20),
        (49, 50),
    ],
)
def test_crossing_threshold_emits_evolution(before_count: int, expected_threshold: int) -> None:
    """Each non-1 threshold crossing fires exactly one world_evolution event
    for the new threshold."""
    plan = _compute(
        observation=_make_observation(iconic_taxon="Plantae"),
        zone_states=[_zone_state("meadow", count=before_count, tier=1)],
        elements=[_existing_element("meadow", _MEADOW_COARSE_PLANTAE)],
    )

    [transition] = plan.zone_transitions
    assert expected_threshold in transition.crossed_thresholds

    evolution_events = _of_event_type(plan.events, "world_evolution")
    assert len(evolution_events) == 1
    assert evolution_events[0].payload["threshold"] == expected_threshold

    evolution_rewards = _of_reward_type(plan.rewards, "world_evolution")
    # >=1 because tiny surprises also emit world_evolution rewards at 3/5/10.
    assert evolution_rewards


def test_no_evolution_when_not_crossing_threshold() -> None:
    """before_count=6, after_count=7 -> no threshold in (1,3,5,10,20,50)
    crosses -> no evolution event."""
    plan = _compute(
        observation=_make_observation(iconic_taxon="Plantae"),
        zone_states=[_zone_state("meadow", count=6, tier=5)],
        elements=[_existing_element("meadow", _MEADOW_COARSE_PLANTAE)],
    )

    [transition] = plan.zone_transitions
    assert transition.crossed_thresholds == ()
    assert _of_event_type(plan.events, "world_evolution") == []
    assert _of_reward_type(plan.rewards, "world_evolution") == []


# ---------------------------------------------------------------------------
# Relationship moment tests
# ---------------------------------------------------------------------------


def test_relationship_unlocks_when_prerequisites_satisfied() -> None:
    """User already has the meadow Plantae coarse element. This observation
    is a first-find of the monarch (taxon_id 48662, iconic_taxon Insecta).
    Both refs of ``meadow_pollination_moment`` are now satisfied -- the
    relationship element should fire in the same plan."""
    plan = _compute(
        observation=_make_observation(
            taxon_id=_MONARCH_TAXON_ID,
            iconic_taxon="Insecta",
            species_name="Danaus plexippus",
        ),
        zone_states=[_zone_state("meadow", count=5, tier=5)],
        elements=[_existing_element("meadow", _MEADOW_COARSE_PLANTAE)],
    )

    unlocked_ids = {e.element_id for e in plan.elements_to_unlock}
    assert _MEADOW_CHARISMATIC_MONARCH in unlocked_ids
    assert _MEADOW_POLLINATION_MOMENT in unlocked_ids

    relationship_events = _of_event_type(plan.events, "relationship")
    assert len(relationship_events) == 1
    assert relationship_events[0].element_id == _MEADOW_POLLINATION_MOMENT


def test_relationship_does_not_duplicate() -> None:
    """If the user already owns the relationship element, observing the
    same charismatic species again (this time as a repeat-find) must NOT
    re-fire the relationship."""
    plan = _compute(
        observation=_make_observation(
            taxon_id=_MONARCH_TAXON_ID,
            iconic_taxon="Insecta",
            species_name="Danaus plexippus",
            is_first_find=False,
        ),
        zone_states=[_zone_state("meadow", count=12, tier=10)],
        elements=[
            _existing_element("meadow", _MEADOW_COARSE_PLANTAE),
            _existing_element("meadow", _MEADOW_CHARISMATIC_MONARCH, "charismatic"),
            _existing_element("meadow", _MEADOW_POLLINATION_MOMENT, "relationship"),
        ],
    )

    unlocked_ids = {e.element_id for e in plan.elements_to_unlock}
    assert _MEADOW_POLLINATION_MOMENT not in unlocked_ids
    assert _of_event_type(plan.events, "relationship") == []


# ---------------------------------------------------------------------------
# Defensive determinism + safety assertions
# ---------------------------------------------------------------------------


def test_planner_is_deterministic_for_same_inputs() -> None:
    """Same inputs -> byte-identical plan. This is the property a future
    dispatcher replay relies on."""
    obs = _make_observation(iconic_taxon="Plantae")
    plan_a = _compute(observation=obs)
    plan_b = _compute(observation=obs)
    assert plan_a == plan_b


def test_plan_contains_no_precise_location_payload() -> None:
    """Every payload dict the planner emits must NOT carry precise-location
    keys. The planner never reads lat / lng inputs and never writes them."""
    forbidden = {"lat", "lng", "latitude", "longitude", "geohash", "coords"}
    plan = _compute(observation=_make_observation(iconic_taxon="Plantae"))

    payloads: list[dict[str, object]] = []
    payloads.extend(e.payload for e in plan.elements_to_unlock)
    payloads.extend(e.payload for e in plan.events)
    payloads.extend(r.payload for r in plan.rewards)

    for payload in payloads:
        assert not (set(payload) & forbidden), (
            f"plan payload must not carry precise-location keys; got {payload}"
        )


# ---------------------------------------------------------------------------
# Local helpers
# ---------------------------------------------------------------------------


def _of_type(items: list[ElementToUnlock], element_type: str) -> list[ElementToUnlock]:
    return [i for i in items if i.element_type == element_type]


def _of_event_type(items: list[PlannedEvent], event_type: str) -> list[PlannedEvent]:
    return [i for i in items if i.event_type == event_type]


def _of_reward_type(items: list[Reward], reward_type: str) -> list[Reward]:
    return [i for i in items if i.type == reward_type]
