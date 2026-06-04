"""Sanctuary content schema -- single source of truth.

Mirrors `docs/sanctuary.md`. Authors edit JSON files under
`content/sanctuary/` (one file per content kind); `scripts/validate_content.py`
parses each file through the matching per-element model; the whole tree is
then assembled into a `SanctuaryConfig` for cross-reference validation.

Hard invariants enforced here (see `AGENTS.md` and `docs/sanctuary.md` §3):
  * No precise-location template tokens in any kid-facing copy.
  * No social-surface copy (share / friend / post / DM / etc.) -- Phase 1.
  * iNat charismatic taxon_ids are flagged unverified by default; authors
    must set `taxon_id_verified=true` only after manual iNat lookup.

Adding a new element kind: add a `<Name>` model here, add the corresponding
list field to `SanctuaryConfig`, extend the cross-reference checks in
`SanctuaryConfig.cross_references_resolve`. See `docs/sanctuary.md` §12.

Style note: mirrors `backend/app/models/expedition.py` -- flat BaseModel
subclasses, no `model_config`, no `ConfigDict`, no `frozen`, taxon_id as
`Annotated[int, Field(ge=1)]` (not `PositiveInt`). The one intentional
divergence is the `model_validator` on `SanctuaryConfig`, which is the
correct Pydantic v2 idiom for cross-field id resolution that no single
`field_validator` can express.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    Field,
    field_validator,
    model_validator,
)

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

# iNat iconic taxa accepted on CoarseUnlock.iconic_taxa. Superset of the
# expedition `IconicTaxon` Literal in two ways:
#   * adds "Annelida" (worms / earthworms) so a soil zone can wake on a
#     worm find without falling through to the "elsewhere" catch-all;
#   * adds "unknown" so observations whose iconic_taxon could not be
#     resolved at submission time still light up the elsewhere zone.
# Drops "Animalia" because sanctuary routing operates at the finer-grained
# iNat iconic_taxon level (Insecta / Aves / Mammalia / etc.), not at the
# kingdom catch-all.
CoarseIconicTaxon = Literal[
    "Plantae",
    "Insecta",
    "Aves",
    "Mammalia",
    "Amphibia",
    "Reptilia",
    "Actinopterygii",
    "Mollusca",
    "Annelida",
    "Arachnida",
    "Fungi",
    "Chromista",
    "Protozoa",
    "unknown",
]

Season = Literal["spring", "summer", "autumn", "winter"]

# Sound-placeholder vocabulary. Authored-only; the mobile screen renders the
# label/description verbatim as a "coming soon" hint. No audio assets are
# shipped in this PR (see docs/sanctuary.md "Seasonal variants & sound
# placeholders"); the kind here is a future-ready key the asset map will
# resolve when ambient audio lands.
SoundKind = Literal[
    "bird_chirp",
    "pond_ripple",
    "meadow_buzz",
    "wind",
    "frog_croak",
]

# Per `docs/sanctuary.md` §7, deepening thresholds are {1, 3, 5, 10, 20, 50}.
# `TinySurprise` (§6) intentionally lives at the intermediate set only --
# zone wake-up (1) is a `world_unlock`, threshold 20 and 50 are zone-level
# evolutions authored on the zone's signature/weather variants in a later
# PR. Tiny surprises are the low-weight ambient detail at 3, 5, 10.
DeepeningThreshold = Literal[3, 5, 10]


# ---------------------------------------------------------------------------
# Shared copy-policy guard
# ---------------------------------------------------------------------------

# Template tokens that would imply precise-location rendering. Per AGENTS.md
# and `docs/sanctuary.md` §11, the kid experience must not depend on (and
# must not display) precise coordinates, addresses, or place names baked
# into content copy.
_FORBIDDEN_LOCATION_TOKENS: tuple[str, ...] = (
    "{location}",
    "{lat}",
    "{lng}",
    "{lon}",
    "{gps}",
    "{coords}",
    "{address}",
    "{city}",
    "{neighborhood}",
    "{place}",
)

# Phase-1 social-surface vocabulary that must not appear in kid-facing copy.
# Matched as lowercase substrings; authors should phrase nudges without
# invoking sharing / following / commenting verbs.
#
# Substring matching is intentionally simple so authors rewrite rather than
# work around the regex. The known false-positive risk (`post` in `signpost`,
# `share` in `sharecropper`, `follow` in `following`) is documented and
# accepted; in practice the kid-facing voice rules push authors away from
# those words anyway.
_FORBIDDEN_SOCIAL_TOKENS: tuple[str, ...] = (
    "share",
    "friend",
    "post",
    " dm ",
    "comment",
    "follow",
    "tag a friend",
    "show your",
    "send to",
)

# Leaderboard / competitive / streak vocabulary the delight layer must not
# introduce -- identity reflection copy especially has to stay descriptive,
# never comparative or pressure-inducing (per docs/sanctuary.md section 3).
# Matched as lowercase substrings; same authoring discipline as the social
# tokens: rewrite, do not work around.
_FORBIDDEN_LEADERBOARD_TOKENS: tuple[str, ...] = (
    "best",
    "better than",
    "more than other",
    "leaderboard",
    " rank",
    " score",
    "compete",
    "winner",
    " win ",
    "streak",
    "in a row",
    "do not miss",
    "don't miss",
    "consecutive day",
    "days in a row",
)


def _enforce_copy_policy(value: str, field_name: str) -> str:
    """Reject precise-location templates and Phase-1 social-surface copy.

    Applied to every kid-facing string field (titles, details, moods, cue
    text, descriptions). All tokens are pre-lowered; the input is lowered
    once before comparison.
    """
    lowered = value.lower()
    for token in _FORBIDDEN_LOCATION_TOKENS:
        if token in lowered:
            raise ValueError(
                f"{field_name} must not contain location template token "
                f"{token!r} (no precise location in kid-facing copy)"
            )
    for token in _FORBIDDEN_SOCIAL_TOKENS:
        if token in lowered:
            raise ValueError(
                f"{field_name} must not contain social-surface token "
                f"{token!r} (Phase 1 has no public social copy)"
            )
    for token in _FORBIDDEN_LEADERBOARD_TOKENS:
        if token in lowered:
            raise ValueError(
                f"{field_name} must not contain leaderboard / streak token "
                f"{token!r} (descriptive copy only -- no comparison or pressure)"
            )
    return value


def _enforce_snake_case(v: str, kind: str) -> str:
    """Same one-liner the expedition model uses, applied to sanctuary ids.

    Lowercase a-z0-9 and underscores; no other characters. Empty string is
    not allowed (an empty stem cannot equal a content id either way).
    """
    if not v or not v.replace("_", "").isalnum() or v != v.lower():
        raise ValueError(f"{kind} id must be lowercase snake_case")
    return v


# ---------------------------------------------------------------------------
# Zone
# ---------------------------------------------------------------------------


class SanctuaryZone(BaseModel):
    """One sanctuary zone (meadow, woodland, pond, sky, soil, urban, elsewhere)."""

    id: ZoneId
    title: Annotated[str, Field(min_length=1, max_length=60)]
    mood: Annotated[str, Field(min_length=1, max_length=180)]

    @field_validator("title", "mood")
    @classmethod
    def copy_policy(cls, v: str) -> str:
        return _enforce_copy_policy(v, "zone copy")


# ---------------------------------------------------------------------------
# Element: coarse iconic-taxon unlock
# ---------------------------------------------------------------------------


class CoarseUnlock(BaseModel):
    """Broad-category sanctuary element keyed by one or more iNat iconic taxa."""

    id: str
    zone: ZoneId
    iconic_taxa: Annotated[list[CoarseIconicTaxon], Field(min_length=1)]
    title: Annotated[str, Field(min_length=1, max_length=60)]
    detail: Annotated[str, Field(min_length=1, max_length=180)]
    icon: Annotated[str, Field(min_length=1, max_length=120)]

    @field_validator("id")
    @classmethod
    def id_is_snake_case(cls, v: str) -> str:
        return _enforce_snake_case(v, "coarse unlock")

    @field_validator("title", "detail")
    @classmethod
    def copy_policy(cls, v: str) -> str:
        return _enforce_copy_policy(v, "coarse unlock copy")


# ---------------------------------------------------------------------------
# Element: charismatic taxon unlock
# ---------------------------------------------------------------------------


class CharismaticUnlock(BaseModel):
    """Charismatic single-taxon unlock keyed by a verified iNat taxon_id.

    `taxon_id_verified` defaults to False; authors must set it true only
    after manually confirming the numeric id on inaturalist.org. Content
    with `taxon_id_verified=false` is acceptable as a draft but MUST be
    flipped to true before any production release. Enforcement of that
    rule at build time is a follow-up PR (see PR description).
    """

    id: str
    zone: ZoneId
    taxon_id: Annotated[int, Field(ge=1)]
    common_name: Annotated[str, Field(min_length=1, max_length=60)]
    title: Annotated[str, Field(min_length=1, max_length=60)]
    detail: Annotated[str, Field(min_length=1, max_length=180)]
    icon: Annotated[str, Field(min_length=1, max_length=120)]
    taxon_id_verified: bool = False

    @field_validator("id")
    @classmethod
    def id_is_snake_case(cls, v: str) -> str:
        return _enforce_snake_case(v, "charismatic unlock")

    @field_validator("common_name", "title", "detail")
    @classmethod
    def copy_policy(cls, v: str) -> str:
        return _enforce_copy_policy(v, "charismatic unlock copy")


# ---------------------------------------------------------------------------
# Element: relationship moment (cross-element / cross-zone)
# ---------------------------------------------------------------------------


class RelationshipMoment(BaseModel):
    """Cross-element moment: refs two or more existing coarse/charismatic ids.

    Existence of each ref id is enforced at whole-tree validation time on
    `SanctuaryConfig`, not at per-file validation. `zones` is `min_length=1`
    because intra-zone relationships are allowed (e.g. meadow Plantae +
    meadow Monarch); cross-zone moments simply list more than one zone.
    """

    id: str
    zones: Annotated[list[ZoneId], Field(min_length=1)]
    refs: Annotated[list[str], Field(min_length=2)]
    title: Annotated[str, Field(min_length=1, max_length=60)]
    detail: Annotated[str, Field(min_length=1, max_length=180)]
    icon: Annotated[str, Field(min_length=1, max_length=120)]

    @field_validator("id")
    @classmethod
    def id_is_snake_case(cls, v: str) -> str:
        return _enforce_snake_case(v, "relationship moment")

    @field_validator("refs")
    @classmethod
    def refs_are_snake_case(cls, refs: list[str]) -> list[str]:
        for r in refs:
            _enforce_snake_case(r, f"relationship moment ref {r!r}")
        return refs

    @field_validator("title", "detail")
    @classmethod
    def copy_policy(cls, v: str) -> str:
        return _enforce_copy_policy(v, "relationship moment copy")


# ---------------------------------------------------------------------------
# Guide line (general or zone-scoped)
# ---------------------------------------------------------------------------


class GuideLine(BaseModel):
    """One-liner guide copy. `zone=None` is global; otherwise scoped to a zone."""

    id: str
    zone: ZoneId | None = None
    text: Annotated[str, Field(min_length=1, max_length=140)]

    @field_validator("id")
    @classmethod
    def id_is_snake_case(cls, v: str) -> str:
        return _enforce_snake_case(v, "guide line")

    @field_validator("text")
    @classmethod
    def copy_policy(cls, v: str) -> str:
        return _enforce_copy_policy(v, "guide line text")


# ---------------------------------------------------------------------------
# Mystery cue (zone-scoped nudge toward an unlock)
# ---------------------------------------------------------------------------


class MysteryCue(BaseModel):
    """Ambient zone cue with a soft hint about what iconic_taxon would unlock here."""

    id: str
    zone: ZoneId
    text: Annotated[str, Field(min_length=1, max_length=140)]
    unlock_hint: Annotated[str, Field(min_length=1, max_length=140)]

    @field_validator("id")
    @classmethod
    def id_is_snake_case(cls, v: str) -> str:
        return _enforce_snake_case(v, "mystery cue")

    @field_validator("text", "unlock_hint")
    @classmethod
    def copy_policy(cls, v: str) -> str:
        return _enforce_copy_policy(v, "mystery cue copy")


# ---------------------------------------------------------------------------
# Tiny surprise (deepening-threshold ambient detail)
# ---------------------------------------------------------------------------


class TinySurprise(BaseModel):
    """Low-weight zone ambient detail at intermediate deepening thresholds (3, 5, 10)."""

    id: str
    zone: ZoneId
    threshold: DeepeningThreshold
    description: Annotated[str, Field(min_length=1, max_length=180)]

    @field_validator("id")
    @classmethod
    def id_is_snake_case(cls, v: str) -> str:
        return _enforce_snake_case(v, "tiny surprise")

    @field_validator("description")
    @classmethod
    def copy_policy(cls, v: str) -> str:
        return _enforce_copy_policy(v, "tiny surprise description")


# ---------------------------------------------------------------------------
# Seasonal variant (per-element seasonal copy override)
# ---------------------------------------------------------------------------


class SeasonalVariant(BaseModel):
    """Seasonal description attached to an existing coarse/charismatic element."""

    id: str
    element_ref: str
    season: Season
    description: Annotated[str, Field(min_length=1, max_length=180)]

    @field_validator("id")
    @classmethod
    def id_is_snake_case(cls, v: str) -> str:
        return _enforce_snake_case(v, "seasonal variant")

    @field_validator("element_ref")
    @classmethod
    def element_ref_is_snake_case(cls, v: str) -> str:
        return _enforce_snake_case(v, f"seasonal variant element_ref {v!r}")

    @field_validator("description")
    @classmethod
    def copy_policy(cls, v: str) -> str:
        return _enforce_copy_policy(v, "seasonal variant description")


# ---------------------------------------------------------------------------
# Soundscape (sound placeholder for a future ambient audio bed)
# ---------------------------------------------------------------------------


class Soundscape(BaseModel):
    """A placeholder describing a future ambient sound for the Sanctuary.

    No audio assets ship in this PR. The mobile screen renders ``label`` +
    ``description`` as a quiet "coming soon" entry so the kid has a hint
    that the Sanctuary will gain ambient sound later without ever
    auto-playing audio, requesting microphone permission, or adding new
    analytics. The ``kind`` is the asset-key root the asset map will
    resolve when audio lands (e.g. ``sanctuary.sound.bird_chirp``).
    """

    id: str
    kind: SoundKind
    zone: ZoneId | None = None
    label: Annotated[str, Field(min_length=1, max_length=80)]
    description: Annotated[str, Field(min_length=1, max_length=180)]

    @field_validator("id")
    @classmethod
    def id_is_snake_case(cls, v: str) -> str:
        return _enforce_snake_case(v, "soundscape")

    @field_validator("label", "description")
    @classmethod
    def copy_policy(cls, v: str) -> str:
        return _enforce_copy_policy(v, "soundscape copy")


# ---------------------------------------------------------------------------
# Top-level sanctuary config (whole-tree cross-reference validation)
# ---------------------------------------------------------------------------


class IdentityReflection(BaseModel):
    """A descriptive line about the kid's developing Sanctuary identity.

    Selection rules are evaluated server-side at GET /v1/sanctuary/me time:
    the route walks ``config.identity_reflections`` in content order and
    returns the FIRST entry whose rules ALL match the kid's snapshot.
    Content order is therefore the deterministic tie-break -- author
    more-specific entries earlier and the universal fallback last.

    Rule semantics:

    - ``dominant_zone``: matches when that zone has the strict maximum
      ``observation_count`` across all unlocked zones (no ties).
    - ``min_total_observations``: matches when the sum of zone counts is
      at least this value.
    - ``min_element_count``: matches when the user has unlocked at least
      this many ``sanctuary_elements`` rows.
    - ``max_zones_unlocked``: matches when fewer than (strict <) this
      many zones are unlocked.
    - Any rule that is ``None`` is ignored (acts as "no constraint").

    ``text`` is rendered verbatim on the client. Copy must be
    DESCRIPTIVE (no leaderboard / comparison / streak language); the
    copy-policy validator enforces the absence of forbidden tokens.
    """

    id: str
    text: Annotated[str, Field(min_length=1, max_length=140)]
    dominant_zone: ZoneId | None = None
    min_total_observations: Annotated[int, Field(ge=0)] | None = None
    min_element_count: Annotated[int, Field(ge=0)] | None = None
    max_zones_unlocked: Annotated[int, Field(ge=1)] | None = None
    tags: list[str] = Field(default_factory=list)

    @field_validator("id")
    @classmethod
    def id_is_snake_case(cls, v: str) -> str:
        return _enforce_snake_case(v, "identity reflection")

    @field_validator("text")
    @classmethod
    def copy_policy(cls, v: str) -> str:
        return _enforce_copy_policy(v, "identity reflection text")


class SanctuaryConfig(BaseModel):
    """The whole sanctuary content tree, assembled for cross-reference checks.

    Per-file validation happens in `scripts/validate_content.py` against
    the individual element models above. After per-file validation passes,
    the validator gathers all instances into this wrapper to enforce
    cross-file invariants: id uniqueness across element kinds, and the
    existence of every referenced id / zone.

    `model_validator(mode="after")` is the one intentional divergence from
    the expedition pattern: cross-file id resolution needs every list-field
    typed and validated before it runs, which `field_validator` cannot
    express in a single check.
    """

    zones: Annotated[list[SanctuaryZone], Field(min_length=1)]
    coarse_unlocks: list[CoarseUnlock] = Field(default_factory=list)
    charismatic_unlocks: list[CharismaticUnlock] = Field(default_factory=list)
    relationship_moments: list[RelationshipMoment] = Field(default_factory=list)
    guide_lines: list[GuideLine] = Field(default_factory=list)
    mystery_cues: list[MysteryCue] = Field(default_factory=list)
    tiny_surprises: list[TinySurprise] = Field(default_factory=list)
    seasonal_variants: list[SeasonalVariant] = Field(default_factory=list)
    identity_reflections: list[IdentityReflection] = Field(default_factory=list)
    soundscapes: list[Soundscape] = Field(default_factory=list)

    @field_validator("zones")
    @classmethod
    def zone_ids_are_unique(cls, zones: list[SanctuaryZone]) -> list[SanctuaryZone]:
        seen: set[str] = set()
        for z in zones:
            if z.id in seen:
                raise ValueError(f"duplicate zone id: {z.id}")
            seen.add(z.id)
        return zones

    @model_validator(mode="after")
    def cross_references_resolve(self) -> SanctuaryConfig:
        zone_ids: set[str] = {z.id for z in self.zones}

        # Element ids must be unique across coarse and charismatic.
        element_ids: set[str] = set()
        for c in self.coarse_unlocks:
            if c.id in element_ids:
                raise ValueError(f"duplicate element id: {c.id}")
            element_ids.add(c.id)
        for ch in self.charismatic_unlocks:
            if ch.id in element_ids:
                raise ValueError(f"duplicate element id: {ch.id}")
            element_ids.add(ch.id)

        # Coarse/charismatic zone refs must resolve.
        for c in self.coarse_unlocks:
            if c.zone not in zone_ids:
                raise ValueError(f"coarse unlock {c.id!r} references unknown zone {c.zone!r}")
        for ch in self.charismatic_unlocks:
            if ch.zone not in zone_ids:
                raise ValueError(
                    f"charismatic unlock {ch.id!r} references unknown zone {ch.zone!r}"
                )

        # Relationship moment refs and zones must resolve.
        rel_seen: set[str] = set()
        for r in self.relationship_moments:
            if r.id in rel_seen:
                raise ValueError(f"duplicate relationship moment id: {r.id}")
            rel_seen.add(r.id)
            for ref in r.refs:
                if ref not in element_ids:
                    raise ValueError(
                        f"relationship moment {r.id!r} references unknown element "
                        f"{ref!r} (must be a coarse or charismatic id)"
                    )
            for z in r.zones:
                if z not in zone_ids:
                    raise ValueError(f"relationship moment {r.id!r} references unknown zone {z!r}")

        # Guide lines: zone is optional; when set, must resolve.
        guide_seen: set[str] = set()
        for g in self.guide_lines:
            if g.id in guide_seen:
                raise ValueError(f"duplicate guide line id: {g.id}")
            guide_seen.add(g.id)
            if g.zone is not None and g.zone not in zone_ids:
                raise ValueError(f"guide line {g.id!r} references unknown zone {g.zone!r}")

        # Mystery cues: zone required, must resolve.
        cue_seen: set[str] = set()
        for m in self.mystery_cues:
            if m.id in cue_seen:
                raise ValueError(f"duplicate mystery cue id: {m.id}")
            cue_seen.add(m.id)
            if m.zone not in zone_ids:
                raise ValueError(f"mystery cue {m.id!r} references unknown zone {m.zone!r}")

        # Tiny surprises: zone must resolve.
        tiny_seen: set[str] = set()
        for t in self.tiny_surprises:
            if t.id in tiny_seen:
                raise ValueError(f"duplicate tiny surprise id: {t.id}")
            tiny_seen.add(t.id)
            if t.zone not in zone_ids:
                raise ValueError(f"tiny surprise {t.id!r} references unknown zone {t.zone!r}")

        # Seasonal variants: element_ref must resolve.
        season_seen: set[str] = set()
        for sv in self.seasonal_variants:
            if sv.id in season_seen:
                raise ValueError(f"duplicate seasonal variant id: {sv.id}")
            season_seen.add(sv.id)
            if sv.element_ref not in element_ids:
                raise ValueError(
                    f"seasonal variant {sv.id!r} references unknown element "
                    f"{sv.element_ref!r} (must be a coarse or charismatic id)"
                )

        # Identity reflections: ids unique; dominant_zone (when set) must
        # name a real zone.
        ident_seen: set[str] = set()
        for ir in self.identity_reflections:
            if ir.id in ident_seen:
                raise ValueError(f"duplicate identity reflection id: {ir.id}")
            ident_seen.add(ir.id)
            if ir.dominant_zone is not None and ir.dominant_zone not in zone_ids:
                raise ValueError(
                    f"identity reflection {ir.id!r} references unknown zone {ir.dominant_zone!r}"
                )

        # Soundscapes: ids unique; zone (when set) must resolve. At most one
        # entry per (kind, zone) pair -- the mobile screen shows each row
        # once and an authored duplicate would only confuse readers.
        sound_seen: set[str] = set()
        sound_kind_zone_seen: set[tuple[str, str | None]] = set()
        for s in self.soundscapes:
            if s.id in sound_seen:
                raise ValueError(f"duplicate soundscape id: {s.id}")
            sound_seen.add(s.id)
            if s.zone is not None and s.zone not in zone_ids:
                raise ValueError(f"soundscape {s.id!r} references unknown zone {s.zone!r}")
            key = (s.kind, s.zone)
            if key in sound_kind_zone_seen:
                raise ValueError(
                    f"duplicate soundscape kind/zone combination "
                    f"{s.kind!r}/{s.zone!r} (collides with {s.id!r})"
                )
            sound_kind_zone_seen.add(key)

        return self
