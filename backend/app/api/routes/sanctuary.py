"""Sanctuary read API.

Single endpoint: ``GET /v1/sanctuary/me``.

Read-only, current-user-scoped. Merges authored content from
``content/sanctuary/`` (process-level cache from PR #96) with durable
per-user state from PR #97's four ``sanctuary_*`` tables. The mobile app
renders the response verbatim; the dispatcher's ``WorldHandler`` (PR #99)
is the only writer.

Privacy posture:

- No precise-location field appears in any DTO, payload, or nested dict.
  Zone routing happens in the WorldHandler from ``iconic_taxon`` + the
  content map; the precise lat/lng never reaches this endpoint.
- The route does NOT accept a ``user_id`` query param. Every query
  filters on the verified ``current_user``'s Postgres ``users.id``.
- No admin or public read path exists in this PR.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import desc, select

from app.core.auth import CurrentUserDep
from app.db import models
from app.db.session import DbSessionDep
from app.sanctuary.content import SanctuaryContent, get_sanctuary_content
from app.sanctuary.types import THRESHOLDS, ZONE_IDS, ZoneId

router = APIRouter(prefix="/v1/sanctuary", tags=["sanctuary"])

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_RECENT_EVENTS_LIMIT = 10
_MAX_MYSTERY_CUES = 3

# Authored zone order -- used for both the ``zones`` array shape and the
# ``mystery_cues`` selection order. Must match ``ZONE_IDS`` (already
# author-defined upstream in ``app.sanctuary.types``).
_ZONE_ORDER: tuple[ZoneId, ...] = ZONE_IDS

# Safe fallback copy when an element_id is missing from authored content
# AND the snapshot payload also lacks the relevant keys. Documented in
# docs/sanctuary.md "API: GET /v1/sanctuary/me" -> content-fallback rules.
_FALLBACK_ELEMENT_TITLE = "Sanctuary detail"
_FALLBACK_ELEMENT_DETAIL = "This appeared after one of your observations."
_FALLBACK_ELEMENT_ICON = "sanctuary.element"

# Starter guide line used when the user has zero unlocked zones.
_STARTER_GUIDE_TEXT = "Your Sanctuary is quiet. One real observation can wake it up."

# ---------------------------------------------------------------------------
# DTOs
# ---------------------------------------------------------------------------


class SanctuaryZoneDTO(BaseModel):
    zone_id: ZoneId
    title: str
    mood: str
    description: str
    observation_count: int = Field(ge=0)
    depth_tier: int = Field(ge=0)
    unlocked: bool
    next_threshold: int | None
    accent: str | None = None


class SanctuaryElementDTO(BaseModel):
    element_id: str
    zone_id: ZoneId
    element_type: Literal["coarse", "charismatic", "relationship", "surprise", "signature"]
    title: str
    detail: str
    icon: str
    taxon_id: int | None
    source_observation_id: str | None
    unlocked_at: datetime
    payload: dict[str, Any] = Field(default_factory=dict)


class SanctuaryEventDTO(BaseModel):
    event_type: Literal["world_unlock", "world_evolution", "relationship", "surprise"]
    zone_id: ZoneId | None
    element_id: str | None
    title: str
    detail: str | None
    created_at: datetime
    payload: dict[str, Any] = Field(default_factory=dict)


class SanctuaryGuideMessageDTO(BaseModel):
    speaker: Literal["dragonfly"] = "dragonfly"
    text: str


class SanctuaryMysteryCueDTO(BaseModel):
    zone_id: ZoneId
    title: str
    detail: str


class SanctuaryJournalEntryDTO(BaseModel):
    event_type: Literal["world_unlock", "world_evolution", "relationship", "surprise"]
    zone_id: ZoneId | None
    element_id: str | None
    title: str
    detail: str | None
    created_at: datetime


class SanctuarySnapshotResponse(BaseModel):
    zones: list[SanctuaryZoneDTO]
    elements: list[SanctuaryElementDTO]
    recent_events: list[SanctuaryEventDTO]
    guide_message: SanctuaryGuideMessageDTO
    mystery_cues: list[SanctuaryMysteryCueDTO]
    journal: list[SanctuaryJournalEntryDTO]


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@router.get("/me", response_model=SanctuarySnapshotResponse)
async def get_my_sanctuary(
    current_user: CurrentUserDep,
    session: DbSessionDep,
) -> SanctuarySnapshotResponse:
    """Return the signed-in user's Sanctuary snapshot.

    The endpoint takes NO query parameters -- a caller cannot pass
    ``?user_id=...`` to retrieve someone else's Sanctuary. The
    ``current_user`` is resolved from the bearer token by the auth
    dependency; the Postgres user row is then looked up by Firebase /
    Dragonfly uid (mirroring the observations endpoint's pattern).
    """
    user_row = (
        await session.execute(
            select(models.User).where(models.User.firebase_uid == current_user.uid)
        )
    ).scalar_one_or_none()
    if user_row is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No Postgres user for this identity",
        )

    content = get_sanctuary_content()

    zone_state_rows = (
        (
            await session.execute(
                select(models.SanctuaryZoneState).where(
                    models.SanctuaryZoneState.user_id == user_row.id
                )
            )
        )
        .scalars()
        .all()
    )
    zone_state_by_id: dict[str, models.SanctuaryZoneState] = {
        row.zone_id: row for row in zone_state_rows
    }

    element_rows = (
        (
            await session.execute(
                select(models.SanctuaryElement)
                .where(models.SanctuaryElement.user_id == user_row.id)
                .order_by(models.SanctuaryElement.unlocked_at.asc())
            )
        )
        .scalars()
        .all()
    )

    recent_event_rows = (
        (
            await session.execute(
                select(models.SanctuaryEvent)
                .where(models.SanctuaryEvent.user_id == user_row.id)
                .order_by(desc(models.SanctuaryEvent.created_at))
                .limit(_RECENT_EVENTS_LIMIT)
            )
        )
        .scalars()
        .all()
    )

    zones = [
        _build_zone(zone_id, content, zone_state_by_id.get(zone_id)) for zone_id in _ZONE_ORDER
    ]
    elements = [_build_element(row, content) for row in element_rows]
    recent_events = [_build_event(row) for row in recent_event_rows]
    # Journal reuses the same recent-events query, reversed -- oldest
    # first among the most-recent-10 set. No second DB call.
    journal = [_build_journal_entry(row) for row in reversed(recent_event_rows)]
    guide_message = _select_guide(content, zones, recent_event_rows)
    mystery_cues = _select_mystery_cues(content, zones)

    return SanctuarySnapshotResponse(
        zones=zones,
        elements=elements,
        recent_events=recent_events,
        guide_message=guide_message,
        mystery_cues=mystery_cues,
        journal=journal,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _next_threshold(observation_count: int) -> int | None:
    """Return the smallest threshold strictly greater than ``count``, or
    ``None`` when the user has met the top threshold.

    Brief table:
      count=0 -> 1; count=1 -> 3; count=2 -> 3; count=3 -> 5;
      count=9 -> 10; count=49 -> 50; count=50 -> None.
    """
    for threshold in THRESHOLDS:
        if observation_count < threshold:
            return threshold
    return None


def _depth_tier(observation_count: int) -> int:
    """Highest threshold crossed by ``count``. 0 when locked."""
    crossed = [t for t in THRESHOLDS if t <= observation_count]
    return crossed[-1] if crossed else 0


def _build_zone(
    zone_id: ZoneId,
    content: SanctuaryContent,
    state: models.SanctuaryZoneState | None,
) -> SanctuaryZoneDTO:
    zone = content.zone_by_id.get(zone_id)
    title = zone.title if zone is not None else zone_id
    mood = zone.mood if zone is not None else ""
    # Pydantic zone model has no `description` or `accent` fields in
    # PR #96; we surface mood as the description and leave accent None
    # for now. Authoring rule for the future content model can add
    # explicit fields without breaking the wire shape.
    description = mood
    observation_count = state.observation_count if state is not None else 0
    depth_tier = state.depth_tier if state is not None else 0
    unlocked = observation_count > 0
    return SanctuaryZoneDTO(
        zone_id=zone_id,
        title=title,
        mood=mood,
        description=description,
        observation_count=observation_count,
        depth_tier=depth_tier,
        unlocked=unlocked,
        next_threshold=_next_threshold(observation_count),
        accent=None,
    )


def _build_element(
    row: models.SanctuaryElement,
    content: SanctuaryContent,
) -> SanctuaryElementDTO:
    """Merge a stored element row with authored content.

    Priority for ``title`` / ``detail`` / ``icon``:
      1. Authored coarse / charismatic / relationship / surprise content
         keyed by ``element_id``.
      2. Snapshot copy on ``SanctuaryElement.payload``.
      3. Safe fallback strings.
    """
    payload: dict[str, Any] = dict(row.payload) if isinstance(row.payload, dict) else {}

    authored_title: str | None = None
    authored_detail: str | None = None
    authored_icon: str | None = None

    coarse = content.coarse_by_id.get(row.element_id)
    if coarse is not None:
        authored_title = coarse.title
        authored_detail = coarse.detail
        authored_icon = coarse.icon

    charismatic = content.charismatic_by_id.get(row.element_id)
    if charismatic is not None:
        authored_title = authored_title or charismatic.title
        authored_detail = authored_detail or charismatic.detail
        authored_icon = authored_icon or charismatic.icon

    for relationship in content.relationships:
        if relationship.id == row.element_id:
            authored_title = authored_title or relationship.title
            authored_detail = authored_detail or relationship.detail
            authored_icon = authored_icon or relationship.icon
            break

    for surprise in content.tiny_surprises:
        if surprise.id == row.element_id:
            authored_detail = authored_detail or surprise.description
            break

    title = authored_title or _string_or_none(payload.get("title")) or _FALLBACK_ELEMENT_TITLE
    detail = authored_detail or _string_or_none(payload.get("detail")) or _FALLBACK_ELEMENT_DETAIL
    icon = authored_icon or _string_or_none(payload.get("icon")) or _FALLBACK_ELEMENT_ICON

    return SanctuaryElementDTO(
        element_id=row.element_id,
        zone_id=row.zone_id,
        element_type=row.element_type,
        title=title,
        detail=detail,
        icon=icon,
        taxon_id=row.taxon_id,
        source_observation_id=row.source_observation_id,
        unlocked_at=row.unlocked_at,
        payload=payload,
    )


def _build_event(row: models.SanctuaryEvent) -> SanctuaryEventDTO:
    return SanctuaryEventDTO(
        event_type=row.event_type,
        zone_id=row.zone_id,
        element_id=row.element_id,
        title=row.title,
        detail=row.detail,
        created_at=row.created_at,
        payload=dict(row.payload) if isinstance(row.payload, dict) else {},
    )


def _build_journal_entry(row: models.SanctuaryEvent) -> SanctuaryJournalEntryDTO:
    return SanctuaryJournalEntryDTO(
        event_type=row.event_type,
        zone_id=row.zone_id,
        element_id=row.element_id,
        title=row.title,
        detail=row.detail,
        created_at=row.created_at,
    )


def _select_guide(
    content: SanctuaryContent,
    zones: list[SanctuaryZoneDTO],
    recent_event_rows: Sequence[models.SanctuaryEvent],
) -> SanctuaryGuideMessageDTO:
    """Deterministic 4-step ladder per the brief.

    1. Zero unlocked zones -> starter line (first general guide line, or
       the hard-coded fallback if no general lines authored).
    2. Most-recent event has a zone AND a zone-specific guide line is
       authored -> that line.
    3. Otherwise, the deepest unlocked zone has a guide line authored
       -> that line.
    4. Otherwise, the first general guide line, or the hard-coded
       fallback if none authored.

    "First matching" iterates ``content.guide_lines`` in content order.
    """
    unlocked_zones = [z for z in zones if z.unlocked]

    if not unlocked_zones:
        for guide in content.guide_lines:
            if guide.zone is None:
                return SanctuaryGuideMessageDTO(text=guide.text)
        return SanctuaryGuideMessageDTO(text=_STARTER_GUIDE_TEXT)

    if recent_event_rows:
        recent_zone = recent_event_rows[0].zone_id
        if recent_zone is not None:
            for guide in content.guide_lines:
                if guide.zone == recent_zone:
                    return SanctuaryGuideMessageDTO(text=guide.text)

    # Step 3: deepest unlocked zone -- sort by depth_tier desc then by
    # the authored zone order for stability.
    by_depth = sorted(
        unlocked_zones,
        key=lambda z: (-z.depth_tier, _ZONE_ORDER.index(z.zone_id)),
    )
    deepest_zone_id = by_depth[0].zone_id
    for guide in content.guide_lines:
        if guide.zone == deepest_zone_id:
            return SanctuaryGuideMessageDTO(text=guide.text)

    # Step 4: any general line, else fallback.
    for guide in content.guide_lines:
        if guide.zone is None:
            return SanctuaryGuideMessageDTO(text=guide.text)
    return SanctuaryGuideMessageDTO(text=_STARTER_GUIDE_TEXT)


def _select_mystery_cues(
    content: SanctuaryContent,
    zones: list[SanctuaryZoneDTO],
) -> list[SanctuaryMysteryCueDTO]:
    """1-3 cues. Preference order:

    1. Locked zones (observation_count == 0) -- the kid has not seen
       this zone yet.
    2. Quiet unlocked zones (observation_count < 3) -- the kid has
       just brushed the zone.
    3. If still under 3 cues, fall back to the lowest-tier remaining
       zones in authored order.

    Cues are emitted in authored zone order (meadow, woodland, pond,
    sky, soil, urban, elsewhere) so the surface stays stable across
    requests.
    """
    by_zone = {z.zone_id: z for z in zones}

    locked: list[ZoneId] = []
    quiet: list[ZoneId] = []
    others: list[ZoneId] = []
    for zone_id in _ZONE_ORDER:
        z = by_zone[zone_id]
        if not z.unlocked:
            locked.append(zone_id)
        elif z.observation_count < 3:
            quiet.append(zone_id)
        else:
            others.append(zone_id)

    candidates: list[ZoneId] = []
    candidates.extend(locked)
    candidates.extend(quiet)
    candidates.extend(others)

    cues: list[SanctuaryMysteryCueDTO] = []
    seen_zones: set[str] = set()
    for zone_id in candidates:
        if zone_id in seen_zones:
            continue
        cue = content.mystery_cue_by_zone.get(zone_id)
        if cue is None:
            continue
        cues.append(
            SanctuaryMysteryCueDTO(
                zone_id=zone_id,
                title=cue.text,
                detail=cue.unlock_hint,
            )
        )
        seen_zones.add(zone_id)
        if len(cues) >= _MAX_MYSTERY_CUES:
            break

    return cues


def _string_or_none(value: object) -> str | None:
    return value if isinstance(value, str) and value else None
