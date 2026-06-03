"""Types and module-level constants for the Sanctuary planner.

All inputs and outputs are plain data (frozen dataclasses + Literal types +
primitives). No database rows, no async tasks, no callables -- the planner
is a pure function over these types and a ``SanctuaryContent`` snapshot.

The values here mirror:
- ``app.models.sanctuary`` (the Pydantic content schema -- PR #96), and
- ``app.db.models`` ``SanctuaryZoneState`` / ``SanctuaryElement`` /
  ``SanctuaryEvent`` ``CheckConstraint`` enums (PR #97).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Literal

# ---------------------------------------------------------------------------
# Vocabularies
# ---------------------------------------------------------------------------

ZoneId = Literal[
    "meadow",
    "woodland",
    "pond",
    "sky",
    "soil",
    "urban",
    "elsewhere",
]

# Matches the SanctuaryElement.element_type CheckConstraint.
ElementType = Literal[
    "coarse",
    "charismatic",
    "relationship",
    "surprise",
    "signature",
]

# Matches the SanctuaryEvent.event_type CheckConstraint.
EventType = Literal[
    "world_unlock",
    "world_evolution",
    "relationship",
    "surprise",
]

# Per docs/sanctuary.md section 7 -- per-zone, per-user deepening thresholds.
# Threshold 1 is the zone wake-up (emitted as world_unlock by the coarse-unlock
# branch); 3, 5, 10, 20, 50 emit world_evolution events.
THRESHOLDS: tuple[int, ...] = (1, 3, 5, 10, 20, 50)

# Per docs/sanctuary.md section 8 -- reward weights for the celebration sort.
WORLD_UNLOCK_WEIGHT: int = 40
WORLD_EVOLUTION_WEIGHT: int = 30

ZONE_IDS: tuple[ZoneId, ...] = (
    "meadow",
    "woodland",
    "pond",
    "sky",
    "soil",
    "urban",
    "elsewhere",
)


# ---------------------------------------------------------------------------
# Reward
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Reward:
    """Dispatcher reward payload. Mirrors the existing handler ``Reward`` shape
    but lives in this package to keep the service decoupled from the
    dispatcher module."""

    type: str
    title: str
    detail: str | None
    icon: str
    weight: int
    payload: dict[str, object]


# ---------------------------------------------------------------------------
# Input snapshots
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ZoneStateSnapshot:
    """Read-only view of one ``sanctuary_zone_state`` row.

    Holds the per-zone count and tier BEFORE the current observation is
    applied. The planner consumes a list of these (one per existing zone for
    the user) and computes the per-zone transition for the contribution
    zone.
    """

    user_id: str
    zone_id: ZoneId
    observation_count: int
    depth_tier: int


@dataclass(frozen=True, slots=True)
class ElementSnapshot:
    """Read-only view of one ``sanctuary_elements`` row.

    The planner uses these to enforce idempotency (a coarse / charismatic /
    relationship / surprise element that already exists for the user does
    NOT fire again).
    """

    user_id: str
    zone_id: ZoneId
    element_id: str
    element_type: ElementType


@dataclass(frozen=True, slots=True)
class ObservationInput:
    """The observation-shaped half of the planner inputs.

    The planner does not read precise location. ``iconic_taxon`` is optional
    because the species_cache lookup may not yet have resolved a value.
    ``current_date`` is reserved for future seasonal logic; it is included
    in the contract now so callers do not have to add it later.
    """

    user_id: str
    observation_id: str
    taxon_id: int | None
    species_name: str | None
    iconic_taxon: str | None
    is_first_find: bool
    current_date: date


@dataclass(frozen=True, slots=True)
class ServiceInputs:
    """All data the planner needs to produce a ``SanctuaryPlan``."""

    observation: ObservationInput
    zone_states: list[ZoneStateSnapshot] = field(default_factory=list)
    elements: list[ElementSnapshot] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Output: the SanctuaryPlan and its parts
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ZoneTransition:
    """Per-zone count + tier delta the planner computed.

    ``before_count`` / ``after_count`` and ``before_tier`` / ``after_tier``
    are the values the handler should write back to ``sanctuary_zone_state``.
    ``crossed_thresholds`` is the strictly-increasing list of thresholds that
    moved from "below" to "at or above" on this observation.
    """

    zone_id: ZoneId
    before_count: int
    after_count: int
    before_tier: int
    after_tier: int
    crossed_thresholds: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class ElementToUnlock:
    """Planned ``sanctuary_elements`` row insert.

    ``payload`` carries the reward-shape snapshot the handler stamps on the
    element row (mirroring the existing ``observations.rewards`` JSONB
    pattern from PR's earlier). The handler is responsible for assigning a
    ULID id at write time.
    """

    zone_id: ZoneId
    element_id: str
    element_type: ElementType
    taxon_id: int | None
    payload: dict[str, object]


@dataclass(frozen=True, slots=True)
class PlannedEvent:
    """Planned ``sanctuary_events`` row insert.

    Append-only. Drives both the celebration sequence on submit and the
    per-zone journal/timeline screen documented in ``docs/sanctuary.md``
    section 10.
    """

    event_type: EventType
    zone_id: ZoneId | None
    element_id: str | None
    title: str
    detail: str | None
    payload: dict[str, object]


@dataclass(frozen=True, slots=True)
class SanctuaryPlan:
    """The planner's full output for one observation.

    A thin handler wrapper translates this into:
      - ``INSERT ... ON CONFLICT DO NOTHING`` on ``sanctuary_elements`` for
        each ``ElementToUnlock``,
      - ``UPDATE sanctuary_zone_state SET observation_count = ..., depth_tier
        = ...`` for each ``ZoneTransition``,
      - ``INSERT`` rows into ``sanctuary_events`` for each ``PlannedEvent``,
      - Append the ``rewards`` to the dispatcher's reward list for the
        celebration sequence.

    The plan is deterministic in all fields including event order: the
    same ``(inputs, content)`` produces a byte-identical output.
    """

    observation_id: str
    contribution_zone_id: ZoneId
    zone_transitions: list[ZoneTransition] = field(default_factory=list)
    elements_to_unlock: list[ElementToUnlock] = field(default_factory=list)
    events: list[PlannedEvent] = field(default_factory=list)
    rewards: list[Reward] = field(default_factory=list)
