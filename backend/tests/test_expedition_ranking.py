"""Tests for `app.services.expedition_ranking` (region-aware /available)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.expedition import (
    Expedition,
    MatchAllOf,
    MatchAnyOf,
    MatchIconicTaxon,
    MatchNotInDex,
    MatchSpec,
    MatchTaxonId,
    Step,
)
from app.services.expedition_ranking import (
    GEOHASH4_RE,
    region_iconic_taxa,
    relevance_for,
    required_iconic_taxa,
)


def _expedition(*matches: MatchSpec) -> Expedition:
    return Expedition(
        id="ranking_probe",
        title="Ranking probe",
        tier=2,
        duration_minutes=20,
        environments=["yard"],
        intro="Find some things.",
        outro="Real science.",
        steps=[Step(id=f"s{i}", description="x", match=match) for i, match in enumerate(matches)],
    )


# ---------------------------------------------------------------------------
# GEOHASH4_RE
# ---------------------------------------------------------------------------


def test_geohash4_re_accepts_base32_and_rejects_the_rest() -> None:
    assert GEOHASH4_RE.fullmatch("9q5c") is not None
    assert GEOHASH4_RE.fullmatch("dp1b") is not None
    # a/i/l/o are not in the geohash base32 alphabet.
    assert GEOHASH4_RE.fullmatch("ailo") is None
    # Wrong length.
    assert GEOHASH4_RE.fullmatch("9q5") is None
    assert GEOHASH4_RE.fullmatch("9q5cd") is None


# ---------------------------------------------------------------------------
# required_iconic_taxa
# ---------------------------------------------------------------------------


def test_required_iconic_taxa_collects_leaf_values() -> None:
    exp = _expedition(
        MatchIconicTaxon(kind="iconic_taxon", value="Aves"),
        MatchIconicTaxon(kind="iconic_taxon", value="Plantae"),
    )
    assert required_iconic_taxa(exp) == frozenset({"Aves", "Plantae"})


def test_required_iconic_taxa_recurses_nested_combinators() -> None:
    """all_of(any_of(plantae, fungi), not_in_dex) -- tier-2 style nesting."""
    spec = MatchAllOf(
        kind="all_of",
        matches=[
            MatchAnyOf(
                kind="any_of",
                matches=[
                    MatchIconicTaxon(kind="iconic_taxon", value="Plantae"),
                    MatchIconicTaxon(kind="iconic_taxon", value="Fungi"),
                ],
            ),
            MatchNotInDex(kind="not_in_dex"),
        ],
    )
    assert required_iconic_taxa(_expedition(spec)) == frozenset({"Plantae", "Fungi"})


def test_required_iconic_taxa_empty_for_taxon_id_only_expedition() -> None:
    exp = _expedition(MatchTaxonId(kind="taxon_id", value=12345))
    assert required_iconic_taxa(exp) == frozenset()


# ---------------------------------------------------------------------------
# relevance_for
# ---------------------------------------------------------------------------


def test_relevance_all_required_present_is_great_here() -> None:
    bucket, level, reason = relevance_for(frozenset({"Aves"}), frozenset({"Aves", "Insecta"}))
    assert bucket == 0
    assert level == "great_here"
    assert reason == "People spot birds near you a lot"


def test_relevance_some_required_present_is_middle_unknown() -> None:
    result = relevance_for(frozenset({"Aves", "Plantae"}), frozenset({"Aves"}))
    assert result == (1, "unknown", None)


def test_relevance_none_present_is_tricky_here() -> None:
    bucket, level, reason = relevance_for(frozenset({"Aves"}), frozenset({"Insecta"}))
    assert bucket == 2
    assert level == "tricky_here"
    assert reason == "Birds are rarely reported near you -- this one is a challenge"


def test_relevance_reasons_stay_kid_readable() -> None:
    """Content voice: no exclamation points, friendly names not Latin."""
    _, _, great = relevance_for(frozenset({"Fungi", "Mollusca"}), frozenset({"Fungi", "Mollusca"}))
    _, _, tricky = relevance_for(frozenset({"Actinopterygii"}), frozenset({"Aves"}))
    assert great == "People spot mushrooms and fungi and snails and shells near you a lot"
    assert tricky == "Fish are rarely reported near you -- this one is a challenge"
    for reason in (great, tricky):
        assert "!" not in reason


def test_relevance_empty_required_is_top_unknown() -> None:
    assert relevance_for(frozenset(), frozenset({"Aves"})) == (0, "unknown", None)


def test_relevance_cold_start_region_is_top_unknown() -> None:
    assert relevance_for(frozenset({"Aves"}), None) == (0, "unknown", None)


# ---------------------------------------------------------------------------
# region_iconic_taxa
# ---------------------------------------------------------------------------


def _scalars_result(values: list[str]) -> MagicMock:
    result = MagicMock()
    scalars = MagicMock()
    scalars.all = MagicMock(return_value=values)
    result.scalars = MagicMock(return_value=scalars)
    return result


async def test_region_iconic_taxa_geohash4_hit_skips_fallback() -> None:
    session = AsyncMock(spec=AsyncSession)
    session.execute = AsyncMock(side_effect=[_scalars_result(["Aves", "Insecta"])])
    assert await region_iconic_taxa(session, "9q5c") == frozenset({"Aves", "Insecta"})
    assert session.execute.await_count == 1


async def test_region_iconic_taxa_falls_back_to_geohash3_prefix() -> None:
    session = AsyncMock(spec=AsyncSession)
    session.execute = AsyncMock(side_effect=[_scalars_result([]), _scalars_result(["Plantae"])])
    assert await region_iconic_taxa(session, "9q5c") == frozenset({"Plantae"})
    assert session.execute.await_count == 2


async def test_region_iconic_taxa_cold_start_returns_none() -> None:
    session = AsyncMock(spec=AsyncSession)
    session.execute = AsyncMock(side_effect=[_scalars_result([]), _scalars_result([])])
    assert await region_iconic_taxa(session, "9q5c") is None
    assert session.execute.await_count == 2
