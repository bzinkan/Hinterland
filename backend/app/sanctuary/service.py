"""Pure-function Sanctuary unlock + evolution planner.

``compute_sanctuary_plan(inputs, content) -> SanctuaryPlan`` is a
side-effect-free computation. It reads the observation-shaped inputs and
the per-user state snapshots, looks up authored Sanctuary content, and
returns a ``SanctuaryPlan`` describing every row the future
``WorldHandler`` should write.

The function performs:
- NO database writes (the handler translates the plan into INSERT/UPDATE),
- NO external HTTP (no iNat, no maps, no geocoding, no moderation),
- NO LLM calls (kid-facing runtime LLM is forbidden by ``AGENTS.md``),
- NO ``datetime.now()`` / ``time.time()`` / ``random`` / ``uuid`` reads
  (deterministic output for replay),
- NO precise-location reads or writes (zones are derived from
  ``iconic_taxon`` + content routing, not from lat/lng).

See ``docs/sanctuary.md`` sections 6-9 for the product contract this
implements.
"""

from __future__ import annotations

from app.sanctuary.content import SanctuaryContent
from app.sanctuary.types import (
    THRESHOLDS,
    WORLD_EVOLUTION_WEIGHT,
    WORLD_UNLOCK_WEIGHT,
    ElementToUnlock,
    ObservationInput,
    PlannedEvent,
    Reward,
    SanctuaryPlan,
    ServiceInputs,
    ZoneId,
    ZoneStateSnapshot,
    ZoneTransition,
)

# Tiny surprises (per docs/sanctuary.md section 6) live only at the
# intermediate thresholds; signature finds at 50 and zone wake-ups at 1
# are their own kinds.
_TINY_SURPRISE_THRESHOLDS: frozenset[int] = frozenset({3, 5, 10})


def compute_sanctuary_plan(
    inputs: ServiceInputs,
    content: SanctuaryContent,
) -> SanctuaryPlan:
    """Compute the Sanctuary plan for one observation. Pure function.

    See module docstring for the side-effect / determinism guarantees.
    """
    obs = inputs.observation
    contribution_zone_id = _resolve_contribution_zone(obs, content)

    transition = _build_zone_transition(
        zone_id=contribution_zone_id,
        zone_states=inputs.zone_states,
    )

    existing_element_ids: set[str] = {e.element_id for e in inputs.elements}

    elements_to_unlock: list[ElementToUnlock] = []
    events: list[PlannedEvent] = []
    rewards: list[Reward] = []

    # Step 3: coarse zone wake-up on first-find. Only fires if iconic_taxon
    # resolves to a CoarseUnlock that lives in the contribution zone, and the
    # element has not already been unlocked for this user.
    coarse = _maybe_coarse_unlock(
        obs=obs,
        contribution_zone_id=contribution_zone_id,
        content=content,
        existing_element_ids=existing_element_ids,
    )
    if coarse is not None:
        elements_to_unlock.append(coarse)
        existing_element_ids.add(coarse.element_id)
        events.append(
            _make_event(
                event_type="world_unlock",
                zone_id=contribution_zone_id,
                element_id=coarse.element_id,
                title=_zone_title(content, contribution_zone_id) + " woke up.",
                detail=content.coarse_by_id[coarse.element_id].detail,
                payload={"unlock_kind": "coarse"},
            )
        )
        rewards.append(
            _make_reward(
                kind="world_unlock",
                title=_zone_title(content, contribution_zone_id) + " woke up.",
                detail=content.coarse_by_id[coarse.element_id].detail,
                icon=content.coarse_by_id[coarse.element_id].icon,
                weight=WORLD_UNLOCK_WEIGHT,
                payload={
                    "zone": contribution_zone_id,
                    "element_id": coarse.element_id,
                    "unlock_kind": "coarse",
                },
            )
        )

    # Step 4: charismatic taxon-specific cameo on first-find.
    charismatic = _maybe_charismatic_unlock(
        obs=obs,
        content=content,
        existing_element_ids=existing_element_ids,
    )
    if charismatic is not None:
        elements_to_unlock.append(charismatic)
        existing_element_ids.add(charismatic.element_id)
        ch = content.charismatic_by_id[charismatic.element_id]
        events.append(
            _make_event(
                event_type="world_unlock",
                zone_id=ch.zone,
                element_id=charismatic.element_id,
                title=ch.title,
                detail=ch.detail,
                payload={"unlock_kind": "charismatic", "taxon_id": ch.taxon_id},
            )
        )
        rewards.append(
            _make_reward(
                kind="world_unlock",
                title=ch.title,
                detail=ch.detail,
                icon=ch.icon,
                weight=WORLD_UNLOCK_WEIGHT,
                payload={
                    "zone": ch.zone,
                    "element_id": charismatic.element_id,
                    "unlock_kind": "charismatic",
                    "taxon_id": ch.taxon_id,
                },
            )
        )

    # Step 5: relationship moments whose refs are now all satisfied.
    for relationship_unlock in _maybe_relationship_unlocks(
        content=content,
        owned_element_ids=existing_element_ids,
    ):
        elements_to_unlock.append(relationship_unlock)
        existing_element_ids.add(relationship_unlock.element_id)
        rm = next(r for r in content.relationships if r.id == relationship_unlock.element_id)
        events.append(
            _make_event(
                event_type="relationship",
                zone_id=relationship_unlock.zone_id,
                element_id=relationship_unlock.element_id,
                title=rm.title,
                detail=rm.detail,
                payload={"refs": list(rm.refs)},
            )
        )
        rewards.append(
            _make_reward(
                kind="world_evolution",
                title=rm.title,
                detail=rm.detail,
                icon=rm.icon,
                weight=WORLD_EVOLUTION_WEIGHT,
                payload={
                    "zone": relationship_unlock.zone_id,
                    "element_id": relationship_unlock.element_id,
                    "unlock_kind": "relationship",
                },
            )
        )

    # Step 6: tiny surprises at intermediate thresholds. Deterministic;
    # threshold-keyed; never randomized.
    for surprise_unlock in _maybe_tiny_surprises(
        contribution_zone_id=contribution_zone_id,
        crossed_thresholds=transition.crossed_thresholds,
        content=content,
        existing_element_ids=existing_element_ids,
    ):
        elements_to_unlock.append(surprise_unlock)
        existing_element_ids.add(surprise_unlock.element_id)
        ts = next(t for t in content.tiny_surprises if t.id == surprise_unlock.element_id)
        zone_title = _zone_title(content, contribution_zone_id)
        events.append(
            _make_event(
                event_type="surprise",
                zone_id=contribution_zone_id,
                element_id=surprise_unlock.element_id,
                title=f"A small detail appears in the {zone_title}.",
                detail=ts.description,
                payload={"threshold": ts.threshold},
            )
        )
        rewards.append(
            _make_reward(
                kind="world_evolution",
                title=f"A small detail appears in the {zone_title}.",
                detail=ts.description,
                icon=f"sanctuary.{contribution_zone_id}.surprise",
                weight=WORLD_EVOLUTION_WEIGHT,
                payload={
                    "zone": contribution_zone_id,
                    "element_id": surprise_unlock.element_id,
                    "threshold": ts.threshold,
                    "unlock_kind": "surprise",
                },
            )
        )

    # Step 7: world_evolution events for every non-1 threshold crossing.
    # Threshold 1 is the zone wake-up, already emitted as world_unlock above
    # (or intentionally suppressed for elsewhere observations with no coarse
    # match -- see docstring on _maybe_coarse_unlock).
    zone_title = _zone_title(content, contribution_zone_id)
    for crossed in transition.crossed_thresholds:
        if crossed == 1:
            continue
        events.append(
            _make_event(
                event_type="world_evolution",
                zone_id=contribution_zone_id,
                element_id=None,
                title=f"Your {zone_title} is fuller.",
                detail=None,
                payload={"threshold": crossed},
            )
        )
        rewards.append(
            _make_reward(
                kind="world_evolution",
                title=f"Your {zone_title} is fuller.",
                detail=None,
                icon=f"sanctuary.{contribution_zone_id}.evolution.{crossed}",
                weight=WORLD_EVOLUTION_WEIGHT,
                payload={
                    "zone": contribution_zone_id,
                    "threshold": crossed,
                    "unlock_kind": "evolution",
                },
            )
        )

    return SanctuaryPlan(
        observation_id=obs.observation_id,
        contribution_zone_id=contribution_zone_id,
        zone_transitions=[transition],
        elements_to_unlock=elements_to_unlock,
        events=events,
        rewards=rewards,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_contribution_zone(
    obs: ObservationInput,
    content: SanctuaryContent,
) -> ZoneId:
    """Pick the single zone this observation contributes to.

    Rules from the brief:
      1. ``iconic_taxon`` set -> route via ``content.coarse_by_iconic_taxon``.
      2. ``iconic_taxon`` unset / unmapped but ``taxon_id`` set -> elsewhere.
      3. ``taxon_id`` also unset -> elsewhere. In practice unreachable
         since 2026-07-03: ``WorldHandler`` skips taxonless observations
         before the planner runs (contributions start at identification).
         Kept so the pure function stays total.
    """
    if obs.iconic_taxon is not None:
        coarse = content.coarse_by_iconic_taxon.get(obs.iconic_taxon)
        if coarse is not None:
            return coarse.zone
    return "elsewhere"


def _build_zone_transition(
    zone_id: ZoneId,
    zone_states: list[ZoneStateSnapshot],
) -> ZoneTransition:
    existing: ZoneStateSnapshot | None = next(
        (s for s in zone_states if s.zone_id == zone_id), None
    )
    before_count = existing.observation_count if existing is not None else 0
    before_tier = existing.depth_tier if existing is not None else 0
    after_count = before_count + 1

    crossed = tuple(t for t in THRESHOLDS if before_count < t <= after_count)
    after_tier = max(crossed) if crossed else before_tier

    return ZoneTransition(
        zone_id=zone_id,
        before_count=before_count,
        after_count=after_count,
        before_tier=before_tier,
        after_tier=after_tier,
        crossed_thresholds=crossed,
    )


def _maybe_coarse_unlock(
    obs: ObservationInput,
    contribution_zone_id: ZoneId,
    content: SanctuaryContent,
    existing_element_ids: set[str],
) -> ElementToUnlock | None:
    """Decide whether this observation wakes up the zone.

    Wakes only on a first-find AND only when ``iconic_taxon`` resolves to a
    ``CoarseUnlock`` whose zone matches the routing decision. If iconic_taxon
    is missing entirely we still route to elsewhere for the count bump but
    DO NOT silently materialize an "elsewhere wake-up" -- the elsewhere zone
    has its own authored ``elsewhere_coarse_unknown`` entry that fires when
    a real iconic_taxon of "unknown" is supplied; speculative pre-iconic
    observations remain quiet until iconic_taxon resolves.
    """
    if not obs.is_first_find:
        return None
    if obs.iconic_taxon is None:
        return None
    coarse = content.coarse_by_iconic_taxon.get(obs.iconic_taxon)
    if coarse is None:
        return None
    if coarse.zone != contribution_zone_id:
        return None
    if coarse.id in existing_element_ids:
        return None
    return ElementToUnlock(
        zone_id=coarse.zone,
        element_id=coarse.id,
        element_type="coarse",
        taxon_id=obs.taxon_id,
        payload={
            "iconic_taxon": obs.iconic_taxon,
            "species_name": obs.species_name,
        },
    )


def _maybe_charismatic_unlock(
    obs: ObservationInput,
    content: SanctuaryContent,
    existing_element_ids: set[str],
) -> ElementToUnlock | None:
    if not obs.is_first_find:
        return None
    if obs.taxon_id is None:
        return None
    ch = content.charismatic_by_taxon_id.get(obs.taxon_id)
    if ch is None:
        return None
    if ch.id in existing_element_ids:
        return None
    return ElementToUnlock(
        zone_id=ch.zone,
        element_id=ch.id,
        element_type="charismatic",
        taxon_id=ch.taxon_id,
        payload={
            "common_name": ch.common_name,
            "species_name": obs.species_name,
        },
    )


def _maybe_relationship_unlocks(
    content: SanctuaryContent,
    owned_element_ids: set[str],
) -> list[ElementToUnlock]:
    """Fire every relationship whose refs are now satisfied.

    Iterates ``content.relationships`` in content order for deterministic
    output. Idempotent: skips any relationship whose id is already in the
    owned set.
    """
    unlocks: list[ElementToUnlock] = []
    for moment in content.relationships:
        if moment.id in owned_element_ids:
            continue
        if not all(ref in owned_element_ids for ref in moment.refs):
            continue
        primary_zone: ZoneId = moment.zones[0]
        unlocks.append(
            ElementToUnlock(
                zone_id=primary_zone,
                element_id=moment.id,
                element_type="relationship",
                taxon_id=None,
                payload={"refs": list(moment.refs)},
            )
        )
    return unlocks


def _maybe_tiny_surprises(
    contribution_zone_id: ZoneId,
    crossed_thresholds: tuple[int, ...],
    content: SanctuaryContent,
    existing_element_ids: set[str],
) -> list[ElementToUnlock]:
    """Fire deterministic tiny surprises authored for the crossed thresholds.

    Iterates ``content.tiny_surprises`` in content order so the same
    ``(zone_id, threshold)`` always picks the same surprise on every replay.
    """
    if not crossed_thresholds:
        return []
    eligible_thresholds = {t for t in crossed_thresholds if t in _TINY_SURPRISE_THRESHOLDS}
    if not eligible_thresholds:
        return []
    unlocks: list[ElementToUnlock] = []
    for ts in content.tiny_surprises:
        if ts.zone != contribution_zone_id:
            continue
        if ts.threshold not in eligible_thresholds:
            continue
        if ts.id in existing_element_ids:
            continue
        unlocks.append(
            ElementToUnlock(
                zone_id=contribution_zone_id,
                element_id=ts.id,
                element_type="surprise",
                taxon_id=None,
                payload={"threshold": ts.threshold},
            )
        )
    return unlocks


def _zone_title(content: SanctuaryContent, zone_id: ZoneId) -> str:
    zone = content.zone_by_id.get(zone_id)
    return zone.title if zone is not None else zone_id


def _make_event(
    event_type: str,
    zone_id: ZoneId | None,
    element_id: str | None,
    title: str,
    detail: str | None,
    payload: dict[str, object],
) -> PlannedEvent:
    # Narrow event_type to the Literal at the call site by trusting the
    # caller; the Literal exists for the DB CheckConstraint match and is
    # validated by the DB on insert.
    from app.sanctuary.types import EventType

    et: EventType = event_type  # type: ignore[assignment]
    return PlannedEvent(
        event_type=et,
        zone_id=zone_id,
        element_id=element_id,
        title=title,
        detail=detail,
        payload=payload,
    )


def _make_reward(
    kind: str,
    title: str,
    detail: str | None,
    icon: str,
    weight: int,
    payload: dict[str, object],
) -> Reward:
    return Reward(
        type=kind,
        title=title,
        detail=detail,
        icon=icon,
        weight=weight,
        payload=payload,
    )
