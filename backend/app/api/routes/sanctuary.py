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
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import desc, select

from app.core.auth import CurrentUserDep, resolve_current_user_row
from app.db import models
from app.db.session import DbSessionDep
from app.models.sanctuary import Season, SoundKind
from app.sanctuary.content import SanctuaryContent, get_sanctuary_content
from app.sanctuary.season import current_season
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

# Per-season visual palette. These are structural rendering hints (a calm
# tone word + one short label per zone), not motivational kid-facing copy
# from scratch -- they are the seasonal analogue of the per-zone hex tints
# already baked into the mobile screen (``ZONE_TOKENS``). The mobile client
# composes them with the authored zone titles already shipped via PR #96.
#
# Northern Hemisphere palette only; see ``app.sanctuary.season`` for the
# documented limitation and the Phase 3 ``SeasonHandler`` follow-up.
_BACKGROUND_TONE_BY_SEASON: dict[Season, str] = {
    "spring": "fresh",
    "summer": "warm",
    "autumn": "fading",
    "winter": "still",
}

_ZONE_ACCENTS_BY_SEASON: dict[Season, dict[ZoneId, str]] = {
    "spring": {
        "meadow": "wildflowers waking",
        "woodland": "buds opening",
        "pond": "water warming",
        "sky": "birds returning",
        "soil": "shoots through leaf litter",
        "urban": "weeds in cracks",
        "elsewhere": "first wandering",
    },
    "summer": {
        "meadow": "tall grass and pollen",
        "woodland": "deep green canopy",
        "pond": "midday glints",
        "sky": "high cumulus",
        "soil": "warm under bark",
        "urban": "shade in the park",
        "elsewhere": "long evenings",
    },
    "autumn": {
        "meadow": "seed heads leaning",
        "woodland": "color in the leaves",
        "pond": "first frost edge",
        "sky": "geese moving on",
        "soil": "mushrooms after rain",
        "urban": "leaf piles on the curb",
        "elsewhere": "quiet light",
    },
    "winter": {
        "meadow": "dry stems standing",
        "woodland": "bare branches",
        "pond": "still water",
        "sky": "low grey ceiling",
        "soil": "frozen under leaf litter",
        "urban": "salt and breath fog",
        "elsewhere": "the long pause",
    },
}

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
    # The kid's own source photo for this element, present ONLY when the
    # source observation's moderation_status is "clean" -- see the
    # photo-enrichment block in ``get_my_sanctuary`` for the safety
    # rationale. ``None`` means "render the icon", never "photo pending":
    # the client gets no moderation state machine.
    photo_id: str | None = None
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
    speaker: Literal["guide"] = "guide"
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


class SanctuaryIdentityReflectionDTO(BaseModel):
    """A descriptive line about the kid's developing Sanctuary identity.

    Selected server-side from authored content via the deterministic
    rule-ladder in ``_select_identity_reflection``. Client renders
    ``text`` verbatim; the ``id`` is included so the client can
    deduplicate / track which line was shown without a string compare.
    """

    id: str
    text: str


class SanctuaryRelationshipMomentDTO(BaseModel):
    """A relationship-moment ``sanctuary_elements`` row, projected for the UI.

    Slim shape on purpose: the screen renders title + detail + icon, and
    the client uses ``element_id`` to deduplicate. The full element row
    (with ``element_type="relationship"`` and ``payload``) is still
    available via ``SanctuarySnapshotResponse.elements`` for callers that
    want it.
    """

    element_id: str
    zone_id: ZoneId
    title: str
    detail: str
    icon: str
    unlocked_at: datetime


class SanctuaryTinySurpriseDTO(BaseModel):
    """A tiny-surprise ``sanctuary_elements`` row, projected for the UI.

    ``threshold`` is the per-zone count at which this surprise fired
    (pulled from the authored ``TinySurprise.threshold``); the client
    uses it as a small "since you crossed N" caption. ``title`` is a
    short caption composed at the route layer from the zone title
    ("A small detail in the Meadow") -- the only client-visible
    string composed by the route; ``detail`` is the authored
    description verbatim.
    """

    element_id: str
    zone_id: ZoneId
    threshold: int | None
    title: str
    detail: str
    icon: str
    unlocked_at: datetime


class SanctuarySeasonDTO(BaseModel):
    """Date-based seasonal tint for the current response.

    Selected from the server date via ``app.sanctuary.season.current_season``
    using a Northern Hemisphere meteorological calendar. The mobile screen
    renders this as a small banner + per-zone accent label; ``variant_copy``
    is the only kid-facing prose and comes verbatim from authored
    ``content/sanctuary/seasonal_variants.json`` when an authored variant
    matches the current season (preferring one whose ``element_ref`` the
    kid has already unlocked).
    """

    season: Season
    background_tone: str
    zone_accents: dict[ZoneId, str]
    variant_copy: str | None


class SanctuarySoundscapeDTO(BaseModel):
    """A placeholder describing a future ambient sound for the Sanctuary.

    No audio assets ship with this DTO -- the mobile screen renders
    ``label`` + ``description`` as a quiet "coming soon" entry. The route
    sets ``sound_assets_available`` on the snapshot to ``false`` until a
    later content drop adds real audio files; the schema below is the
    future-ready shape the client will read once that lands.
    """

    id: str
    kind: SoundKind
    zone_id: ZoneId | None
    label: str
    description: str


class SanctuarySouvenirDTO(BaseModel):
    """A keepsake for a completed expedition, derived at read time.

    ADR 0012 "Expedition souvenirs": completed ``expedition_progress``
    rows are joined to authored ``content/sanctuary/souvenirs.json`` at
    request time -- no new tables, no new writer, no dispatcher change.
    Progress rows whose expedition has no authored souvenir (content can
    trail) are silently skipped.
    """

    expedition_id: str
    zone_id: ZoneId
    icon: str
    title: str
    detail: str
    completed_at: datetime


class SanctuarySnapshotResponse(BaseModel):
    zones: list[SanctuaryZoneDTO]
    elements: list[SanctuaryElementDTO]
    recent_events: list[SanctuaryEventDTO]
    guide_message: SanctuaryGuideMessageDTO
    mystery_cues: list[SanctuaryMysteryCueDTO]
    journal: list[SanctuaryJournalEntryDTO]
    identity_reflection: SanctuaryIdentityReflectionDTO | None = None
    relationship_moments: list[SanctuaryRelationshipMomentDTO] = Field(default_factory=list)
    tiny_surprises: list[SanctuaryTinySurpriseDTO] = Field(default_factory=list)
    season: SanctuarySeasonDTO
    soundscapes: list[SanctuarySoundscapeDTO] = Field(default_factory=list)
    souvenirs: list[SanctuarySouvenirDTO] = Field(default_factory=list)
    # ``sound_assets_available`` is the global gate the mobile screen uses
    # to decide whether to render a future play control vs. a muted
    # placeholder. False until audio assets ship; flipped on by a later
    # content/asset PR rather than per-soundscape.
    sound_assets_available: bool = False


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
    dependency; the Postgres user row is then resolved to the canonical
    local ``users.id``.
    """
    user_row = await resolve_current_user_row(session, current_user)

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

    # ------------------------------------------------------------------
    # Photo enrichment -- SAFETY-CRITICAL gate (ADR 0012 "Photo-on-tap").
    #
    # The Sanctuary is a kid-facing surface, so the clean-moderation gate
    # lives HERE, server-side: an element carries a ``photo_id`` only when
    # its source observation belongs to the current user AND its
    # ``moderation_status`` is ``"clean"``. The photo-URL endpoint
    # (``GET /v1/photos/{photo_id}/url``, post-#161) enforces its own
    # audience rules -- adults (parent/teacher group-mates) may fetch
    # UNMODERATED photos there because that endpoint is the review
    # surface, and owners may fetch any non-deleted photo of their own --
    # so relying on it alone would let pending/quarantined photos render
    # in the Sanctuary. Gating on "clean" here means the client never
    # learns a photo_id it should not show; a pending photo simply
    # appears on a later refresh once moderation passes, with no
    # client-side moderation state machine. (Re-validated against
    # post-#161 ``photos.py``: the owner check there short-circuits every
    # non-owner rule, so a kid CAN fetch the signed URL for their own
    # clean photo -- this surface is not dead-on-arrival.)
    # ------------------------------------------------------------------
    source_observation_ids = {
        row.source_observation_id for row in element_rows if row.source_observation_id is not None
    }
    photo_id_by_observation_id: dict[str, str] = {}
    if source_observation_ids:
        observation_rows = (
            await session.execute(
                select(
                    models.Observation.id,
                    models.Observation.photo_id,
                    models.Observation.moderation_status,
                ).where(
                    models.Observation.id.in_(source_observation_ids),
                    # Defensive ownership pin: a sanctuary_elements row is
                    # already user-scoped, but its source_observation_id
                    # column has no same-user constraint -- never let a
                    # foreign observation id resolve to a photo here.
                    models.Observation.user_id == user_row.id,
                )
            )
        ).all()
        for observation_id, photo_id, moderation_status in observation_rows:
            if moderation_status == "clean":
                photo_id_by_observation_id[observation_id] = photo_id

    # Completed expeditions -> read-time souvenirs (ADR 0012). Oldest
    # completion first so the shelf grows at the end as new expeditions
    # finish.
    progress_rows = (
        await session.execute(
            select(
                models.ExpeditionProgress.expedition_id,
                models.ExpeditionProgress.completed_at,
            )
            .where(
                models.ExpeditionProgress.user_id == user_row.id,
                models.ExpeditionProgress.completed_at.is_not(None),
            )
            .order_by(
                models.ExpeditionProgress.completed_at.asc(),
                # Tie-break so same-second completions keep a stable shelf order.
                models.ExpeditionProgress.expedition_id.asc(),
            )
        )
    ).all()

    zones = [
        _build_zone(zone_id, content, zone_state_by_id.get(zone_id)) for zone_id in _ZONE_ORDER
    ]
    elements = [_build_element(row, content, photo_id_by_observation_id) for row in element_rows]
    recent_events = [_build_event(row) for row in recent_event_rows]
    # Journal reuses the same recent-events query, reversed -- oldest
    # first among the most-recent-10 set. No second DB call.
    journal = [_build_journal_entry(row) for row in reversed(recent_event_rows)]
    guide_message = _select_guide(content, zones, recent_event_rows)
    mystery_cues = _select_mystery_cues(content, zones)
    identity_reflection = _select_identity_reflection(content, zones, element_rows)
    relationship_moments = _relationship_moments_from_elements(element_rows, content)
    tiny_surprises_view = _tiny_surprises_from_elements(element_rows, content)
    today = datetime.now(tz=UTC).date()
    season_info = _select_season_info(
        season=current_season(today),
        content=content,
        unlocked_element_ids={row.element_id for row in element_rows},
    )
    soundscapes_view = _soundscapes_for_response(content)
    souvenirs_view = _souvenirs_from_progress(progress_rows, content)

    return SanctuarySnapshotResponse(
        zones=zones,
        elements=elements,
        recent_events=recent_events,
        guide_message=guide_message,
        mystery_cues=mystery_cues,
        journal=journal,
        identity_reflection=identity_reflection,
        relationship_moments=relationship_moments,
        tiny_surprises=tiny_surprises_view,
        season=season_info,
        soundscapes=soundscapes_view,
        souvenirs=souvenirs_view,
        sound_assets_available=False,
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
    photo_id_by_observation_id: dict[str, str],
) -> SanctuaryElementDTO:
    """Merge a stored element row with authored content.

    Priority for ``title`` / ``detail`` / ``icon``:
      1. Authored coarse / charismatic / relationship / surprise content
         keyed by ``element_id``.
      2. Snapshot copy on ``SanctuaryElement.payload``.
      3. Safe fallback strings.

    ``photo_id_by_observation_id`` holds ONLY the current user's
    clean-moderated observations (built in ``get_my_sanctuary``); any
    element whose source observation is absent from the map -- pending,
    quarantined, rejected, foreign, or deleted -- gets ``photo_id=None``.
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

    photo_id: str | None = None
    if row.source_observation_id is not None:
        photo_id = photo_id_by_observation_id.get(row.source_observation_id)

    return SanctuaryElementDTO(
        element_id=row.element_id,
        zone_id=row.zone_id,
        element_type=row.element_type,
        title=title,
        detail=detail,
        icon=icon,
        taxon_id=row.taxon_id,
        source_observation_id=row.source_observation_id,
        photo_id=photo_id,
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


def _select_identity_reflection(
    content: SanctuaryContent,
    zones: list[SanctuaryZoneDTO],
    element_rows: Sequence[models.SanctuaryElement],
) -> SanctuaryIdentityReflectionDTO | None:
    """Pick a descriptive identity-reflection line via author-time rules.

    Walks ``content.identity_reflections`` in content order and returns the
    FIRST entry whose rules ALL match the kid's snapshot. Content order is
    the deterministic tie-break -- author more-specific entries earlier
    and the universal fallback last.

    Returns ``None`` only when no entries are authored (early-content
    state).
    """
    if not content.identity_reflections:
        return None

    unlocked = [z for z in zones if z.unlocked]
    total_observations = sum(z.observation_count for z in unlocked)
    element_count = len(element_rows)
    zones_unlocked = len(unlocked)

    # Dominant zone = strict maximum; ties produce no dominant zone.
    dominant_zone: ZoneId | None = None
    if unlocked:
        by_count = sorted(unlocked, key=lambda z: z.observation_count, reverse=True)
        if len(by_count) == 1 or by_count[0].observation_count > by_count[1].observation_count:
            dominant_zone = by_count[0].zone_id

    for reflection in content.identity_reflections:
        if reflection.dominant_zone is not None and reflection.dominant_zone != dominant_zone:
            continue
        if (
            reflection.min_total_observations is not None
            and total_observations < reflection.min_total_observations
        ):
            continue
        if (
            reflection.min_element_count is not None
            and element_count < reflection.min_element_count
        ):
            continue
        if (
            reflection.max_zones_unlocked is not None
            and zones_unlocked >= reflection.max_zones_unlocked
        ):
            continue
        return SanctuaryIdentityReflectionDTO(id=reflection.id, text=reflection.text)

    return None


def _relationship_moments_from_elements(
    element_rows: Sequence[models.SanctuaryElement],
    content: SanctuaryContent,
) -> list[SanctuaryRelationshipMomentDTO]:
    """Project relationship ``sanctuary_elements`` rows into the slim DTO.

    Authored ``RelationshipMoment.title`` / ``detail`` / ``icon`` win;
    snapshot payload + safe fallback follow the same chain as
    ``_build_element``.
    """
    moments_by_id = {r.id: r for r in content.relationships}
    out: list[SanctuaryRelationshipMomentDTO] = []
    for row in element_rows:
        if row.element_type != "relationship":
            continue
        payload: dict[str, Any] = dict(row.payload) if isinstance(row.payload, dict) else {}
        authored = moments_by_id.get(row.element_id)
        title = (
            (authored.title if authored is not None else None)
            or _string_or_none(payload.get("title"))
            or _FALLBACK_ELEMENT_TITLE
        )
        detail = (
            (authored.detail if authored is not None else None)
            or _string_or_none(payload.get("detail"))
            or _FALLBACK_ELEMENT_DETAIL
        )
        icon = (
            (authored.icon if authored is not None else None)
            or _string_or_none(payload.get("icon"))
            or _FALLBACK_ELEMENT_ICON
        )
        out.append(
            SanctuaryRelationshipMomentDTO(
                element_id=row.element_id,
                zone_id=row.zone_id,
                title=title,
                detail=detail,
                icon=icon,
                unlocked_at=row.unlocked_at,
            )
        )
    return out


def _tiny_surprises_from_elements(
    element_rows: Sequence[models.SanctuaryElement],
    content: SanctuaryContent,
) -> list[SanctuaryTinySurpriseDTO]:
    """Project surprise ``sanctuary_elements`` rows into the slim DTO.

    The authored ``TinySurprise`` model has only ``description`` (no
    title); the route synthesizes a calm caption from the zone title --
    e.g. "A small detail in the Meadow" -- which is the only string
    composed at the route layer. ``detail`` is the authored description
    verbatim.
    """
    surprises_by_id = {t.id: t for t in content.tiny_surprises}
    out: list[SanctuaryTinySurpriseDTO] = []
    for row in element_rows:
        if row.element_type != "surprise":
            continue
        payload: dict[str, Any] = dict(row.payload) if isinstance(row.payload, dict) else {}
        authored = surprises_by_id.get(row.element_id)
        zone_id: ZoneId = row.zone_id  # type: ignore[assignment]
        zone = content.zone_by_id.get(zone_id)
        zone_title = zone.title if zone is not None else zone_id
        title = f"A small detail in the {zone_title}"
        detail = (
            (authored.description if authored is not None else None)
            or _string_or_none(payload.get("detail"))
            or _string_or_none(payload.get("description"))
            or _FALLBACK_ELEMENT_DETAIL
        )
        icon = _string_or_none(payload.get("icon")) or f"sanctuary.{row.zone_id}.surprise"
        threshold_raw = payload.get("threshold")
        threshold: int | None = (
            authored.threshold
            if authored is not None
            else (threshold_raw if isinstance(threshold_raw, int) else None)
        )
        out.append(
            SanctuaryTinySurpriseDTO(
                element_id=row.element_id,
                zone_id=row.zone_id,
                threshold=threshold,
                title=title,
                detail=detail,
                icon=icon,
                unlocked_at=row.unlocked_at,
            )
        )
    return out


def _select_season_info(
    *,
    season: Season,
    content: SanctuaryContent,
    unlocked_element_ids: set[str],
) -> SanctuarySeasonDTO:
    """Build the per-response seasonal info block.

    - ``season`` and ``background_tone`` come from the date-keyed palette
      above (structural, not kid-facing prose).
    - ``zone_accents`` is the per-zone short label for that season -- still
      structural rendering data, mirroring how ``ZONE_TOKENS`` lives in
      the mobile client today.
    - ``variant_copy`` is the only kid-facing prose and comes verbatim from
      authored ``content/sanctuary/seasonal_variants.json``. The route
      first looks for a season-matching variant whose ``element_ref`` is
      one of the kid's already-unlocked elements; failing that, falls
      back to the first season-matching variant in content order so a
      brand-new kid still sees a seasonal line. Returns ``None`` only
      when no variant is authored for the current season.
    """
    background_tone = _BACKGROUND_TONE_BY_SEASON[season]
    zone_accents = dict(_ZONE_ACCENTS_BY_SEASON[season])

    variant_copy: str | None = None
    season_matched = [sv for sv in content.seasonal_variants if sv.season == season]
    if season_matched:
        for sv in season_matched:
            if sv.element_ref in unlocked_element_ids:
                variant_copy = sv.description
                break
        if variant_copy is None:
            variant_copy = season_matched[0].description

    return SanctuarySeasonDTO(
        season=season,
        background_tone=background_tone,
        zone_accents=zone_accents,
        variant_copy=variant_copy,
    )


def _souvenirs_from_progress(
    progress_rows: Sequence[Any],
    content: SanctuaryContent,
) -> list[SanctuarySouvenirDTO]:
    """Map completed ``expedition_progress`` rows to authored souvenirs.

    ``progress_rows`` are ``(expedition_id, completed_at)`` pairs already
    filtered to ``completed_at IS NOT NULL`` and ordered by completion
    time ascending; that order is preserved on the wire. A row whose
    expedition has no authored souvenir yet is silently skipped --
    content can trail the expedition catalogue without breaking the
    snapshot (the souvenir simply appears once souvenirs.json catches
    up).
    """
    out: list[SanctuarySouvenirDTO] = []
    for expedition_id, completed_at in progress_rows:
        if completed_at is None:  # defensive; the query already filters
            continue
        souvenir = content.souvenir_by_expedition_id.get(expedition_id)
        if souvenir is None:
            continue
        out.append(
            SanctuarySouvenirDTO(
                expedition_id=souvenir.expedition_id,
                zone_id=souvenir.zone,
                icon=souvenir.icon,
                title=souvenir.title,
                detail=souvenir.detail,
                completed_at=completed_at,
            )
        )
    return out


def _soundscapes_for_response(content: SanctuaryContent) -> list[SanctuarySoundscapeDTO]:
    """Project authored ``Soundscape`` entries into the wire DTO.

    No assets ship in this PR; the snapshot's ``sound_assets_available``
    flag stays ``False`` so the client renders a muted placeholder rather
    than any playback control. The endpoint never returns asset URLs,
    bytes, or play tokens.
    """
    return [
        SanctuarySoundscapeDTO(
            id=s.id,
            kind=s.kind,
            zone_id=s.zone,
            label=s.label,
            description=s.description,
        )
        for s in content.soundscapes
    ]
