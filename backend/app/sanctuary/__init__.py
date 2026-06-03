"""Sanctuary computation service.

Pure-function unlock + evolution planner that the future ``WorldHandler``
calls. Persistence-free; the handler is responsible for translating the
plan returned here into the four Sanctuary state tables added in PR #97.

See ``docs/sanctuary.md`` (sections 6, 7, 8, 9) for the product contract.
"""

from app.sanctuary.content import (
    SanctuaryContent,
    get_sanctuary_content,
    reset_sanctuary_content_cache,
)
from app.sanctuary.service import compute_sanctuary_plan
from app.sanctuary.types import (
    THRESHOLDS,
    WORLD_EVOLUTION_WEIGHT,
    WORLD_UNLOCK_WEIGHT,
    ZONE_IDS,
    ElementSnapshot,
    ElementToUnlock,
    EventType,
    ObservationInput,
    PlannedEvent,
    Reward,
    SanctuaryPlan,
    ServiceInputs,
    ZoneId,
    ZoneStateSnapshot,
    ZoneTransition,
)

__all__ = [
    "THRESHOLDS",
    "WORLD_EVOLUTION_WEIGHT",
    "WORLD_UNLOCK_WEIGHT",
    "ZONE_IDS",
    "ElementSnapshot",
    "ElementToUnlock",
    "EventType",
    "ObservationInput",
    "PlannedEvent",
    "Reward",
    "SanctuaryContent",
    "SanctuaryPlan",
    "ServiceInputs",
    "ZoneId",
    "ZoneStateSnapshot",
    "ZoneTransition",
    "compute_sanctuary_plan",
    "get_sanctuary_content",
    "reset_sanctuary_content_cache",
]
