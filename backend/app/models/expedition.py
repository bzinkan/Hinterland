"""Expedition content schema -- single source of truth.

Mirrors `docs/expedition-authoring.md`. Authors edit JSON files under
`content/expeditions/`; `scripts/validate_content.py` parses each file
through this model; `admin.sync_expeditions` (the deployed
`hinterland-sync-expeditions` job; `scripts/sync_expeditions.py` is its
local shim) writes valid files to the `expedition_content` Postgres
table; `app.matchers.registry` interprets each step's `match` block at
observation time.

Adding a new match kind: add a `Match<Name>` model here, add it to the
`MatchSpec` discriminated union, add a matcher in
`app/matchers/kinds/<name>.py`, register it in the matcher registry.
See `docs/expedition-authoring.md` "Adding a new match kind" recipe.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field, field_validator

# ---------------------------------------------------------------------------
# Match specs
# ---------------------------------------------------------------------------

IconicTaxon = Literal[
    "Plantae",
    "Insecta",
    "Aves",
    "Mammalia",
    "Reptilia",
    "Amphibia",
    "Actinopterygii",
    "Mollusca",
    "Arachnida",
    "Fungi",
    "Chromista",
    "Protozoa",
    "Animalia",
]

Environment = Literal["yard", "park", "street", "school", "other"]


class MatchIconicTaxon(BaseModel):
    kind: Literal["iconic_taxon"]
    value: IconicTaxon


class MatchTaxonId(BaseModel):
    kind: Literal["taxon_id"]
    value: Annotated[int, Field(ge=1)]
    include_descendants: bool = True


class MatchAnyOrganism(BaseModel):
    kind: Literal["any_organism"]


class MatchNotInDex(BaseModel):
    kind: Literal["not_in_dex"]


class MatchNotWithinRadius(BaseModel):
    kind: Literal["not_within_radius_of_existing"]
    radius_meters: Annotated[int, Field(ge=1, le=10_000)]


class MatchAllOf(BaseModel):
    kind: Literal["all_of"]
    # min_length guard: an empty all_of would vacuously match ANY photo.
    matches: Annotated[list[MatchSpec], Field(min_length=1)]


class MatchAnyOf(BaseModel):
    kind: Literal["any_of"]
    matches: Annotated[list[MatchSpec], Field(min_length=1)]


MatchSpec = Annotated[
    MatchIconicTaxon
    | MatchTaxonId
    | MatchAnyOrganism
    | MatchNotInDex
    | MatchNotWithinRadius
    | MatchAllOf
    | MatchAnyOf,
    Field(discriminator="kind"),
]

# Resolve forward refs for the recursive combinator types.
MatchAllOf.model_rebuild()
MatchAnyOf.model_rebuild()


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------


class Step(BaseModel):
    id: str
    description: Annotated[str, Field(min_length=1, max_length=120)]
    match: MatchSpec
    hint: str | None = None

    @field_validator("id")
    @classmethod
    def id_is_snake_case(cls, v: str) -> str:
        if not v.replace("_", "").isalnum() or v != v.lower():
            raise ValueError("step id must be lowercase snake_case")
        return v


# ---------------------------------------------------------------------------
# Prerequisites
# ---------------------------------------------------------------------------


class PrereqDexCount(BaseModel):
    kind: Literal["dex_count_at_least"]
    value: Annotated[int, Field(ge=1)]


class PrereqCompleted(BaseModel):
    kind: Literal["completed_expedition"]
    value: str


Prerequisite = Annotated[
    PrereqDexCount | PrereqCompleted,
    Field(discriminator="kind"),
]


# ---------------------------------------------------------------------------
# Expedition
# ---------------------------------------------------------------------------


class Expedition(BaseModel):
    """Top-level expedition. One per JSON file under content/expeditions/."""

    id: str
    title: Annotated[str, Field(min_length=1, max_length=80)]
    subtitle: Annotated[str, Field(max_length=160)] | None = None
    tier: Annotated[int, Field(ge=1, le=5)]
    duration_minutes: Annotated[int, Field(ge=5, le=120)]
    environments: Annotated[list[Environment], Field(min_length=1)]
    intro: Annotated[str, Field(min_length=1, max_length=600)]
    outro: Annotated[str, Field(min_length=1, max_length=300)]
    prerequisites: list[Prerequisite] = Field(default_factory=list)
    steps: Annotated[list[Step], Field(min_length=1, max_length=5)]

    @field_validator("id")
    @classmethod
    def id_is_snake_case(cls, v: str) -> str:
        if not v.replace("_", "").isalnum() or v != v.lower():
            raise ValueError("expedition id must be lowercase snake_case")
        return v

    @field_validator("steps")
    @classmethod
    def step_ids_are_unique(cls, steps: list[Step]) -> list[Step]:
        seen: set[str] = set()
        for s in steps:
            if s.id in seen:
                raise ValueError(f"duplicate step id: {s.id}")
            seen.add(s.id)
        return steps
