"""Per-kind matcher tests + registry tests."""

from __future__ import annotations

from app.matchers.context import MatcherInputs, PriorObservation, TaxonInfo
from app.matchers.registry import matches
from app.models.expedition import (
    MatchAllOf,
    MatchAnyOf,
    MatchAnyOrganism,
    MatchIconicTaxon,
    MatchNotInCurrentExpedition,
    MatchNotInDex,
    MatchNotWithinRadius,
    MatchObservationTag,
    MatchTaxonId,
    MatchTaxonSet,
)


def _inputs(
    *,
    taxon: TaxonInfo | None = None,
    dex: frozenset[int] = frozenset(),
    priors: tuple[PriorObservation, ...] = (),
    taxon_sets: dict[str, frozenset[int]] | None = None,
    current_expedition_taxa: frozenset[int] = frozenset(),
    ecology_tags: dict[str, str] | None = None,
    lat: float = 39.1,
    lng: float = -84.5,
) -> MatcherInputs:
    return MatcherInputs(
        taxon=taxon,
        user_dex_taxon_ids=dex,
        user_prior_observations=priors,
        obs_latitude=lat,
        obs_longitude=lng,
        taxon_sets=taxon_sets or {},
        current_expedition_taxon_ids=current_expedition_taxa,
        ecology_tags=ecology_tags or {},
    )


def _bird_taxon(taxon_id: int = 12345) -> TaxonInfo:
    return TaxonInfo(taxon_id=taxon_id, iconic_taxon="Aves", ancestor_ids=(3, 4, 5))


# ---------------------------------------------------------------------------
# iconic_taxon
# ---------------------------------------------------------------------------


def test_iconic_taxon_matches_exact() -> None:
    spec = MatchIconicTaxon(kind="iconic_taxon", value="Aves")
    assert matches(spec, _inputs(taxon=_bird_taxon())) is True


def test_iconic_taxon_no_match_for_different_kingdom() -> None:
    spec = MatchIconicTaxon(kind="iconic_taxon", value="Plantae")
    assert matches(spec, _inputs(taxon=_bird_taxon())) is False


def test_iconic_taxon_no_match_when_observation_has_no_taxon() -> None:
    spec = MatchIconicTaxon(kind="iconic_taxon", value="Aves")
    assert matches(spec, _inputs(taxon=None)) is False


# ---------------------------------------------------------------------------
# taxon_id
# ---------------------------------------------------------------------------


def test_taxon_id_exact_match() -> None:
    spec = MatchTaxonId(kind="taxon_id", value=12345, include_descendants=False)
    assert matches(spec, _inputs(taxon=_bird_taxon(12345))) is True


def test_taxon_id_no_match_when_different_id_no_descendants() -> None:
    spec = MatchTaxonId(kind="taxon_id", value=999, include_descendants=False)
    assert matches(spec, _inputs(taxon=_bird_taxon(12345))) is False


def test_taxon_id_descendants_match_via_ancestor_chain() -> None:
    """Spec value 4 sits in the bird's ancestor chain (3, 4, 5)."""
    spec = MatchTaxonId(kind="taxon_id", value=4, include_descendants=True)
    assert matches(spec, _inputs(taxon=_bird_taxon(12345))) is True


def test_taxon_id_descendants_off_ignores_ancestor_chain() -> None:
    spec = MatchTaxonId(kind="taxon_id", value=4, include_descendants=False)
    assert matches(spec, _inputs(taxon=_bird_taxon(12345))) is False


def test_taxon_id_no_match_when_observation_has_no_taxon() -> None:
    spec = MatchTaxonId(kind="taxon_id", value=12345)
    assert matches(spec, _inputs(taxon=None)) is False


# ---------------------------------------------------------------------------
# any_organism
# ---------------------------------------------------------------------------


def test_any_organism_matches_when_taxon_present() -> None:
    spec = MatchAnyOrganism(kind="any_organism")
    assert matches(spec, _inputs(taxon=_bird_taxon())) is True


def test_any_organism_no_match_when_no_taxon() -> None:
    spec = MatchAnyOrganism(kind="any_organism")
    assert matches(spec, _inputs(taxon=None)) is False


# ---------------------------------------------------------------------------
# not_in_dex
# ---------------------------------------------------------------------------


def test_not_in_dex_matches_for_unseen_species() -> None:
    spec = MatchNotInDex(kind="not_in_dex")
    inputs = _inputs(taxon=_bird_taxon(12345), dex=frozenset({1, 2, 3}))
    assert matches(spec, inputs) is True


def test_not_in_dex_no_match_when_species_already_in_dex() -> None:
    spec = MatchNotInDex(kind="not_in_dex")
    inputs = _inputs(taxon=_bird_taxon(12345), dex=frozenset({12345, 999}))
    assert matches(spec, inputs) is False


# ---------------------------------------------------------------------------
# not_within_radius_of_existing
# ---------------------------------------------------------------------------


def test_not_within_radius_matches_when_no_priors() -> None:
    spec = MatchNotWithinRadius(kind="not_within_radius_of_existing", radius_meters=50)
    assert matches(spec, _inputs(priors=())) is True


def test_not_within_radius_declines_without_precise_location() -> None:
    spec = MatchNotWithinRadius(kind="not_within_radius_of_existing", radius_meters=50)
    inputs = _inputs(priors=())
    inputs = MatcherInputs(
        taxon=inputs.taxon,
        user_dex_taxon_ids=inputs.user_dex_taxon_ids,
        user_prior_observations=inputs.user_prior_observations,
        obs_latitude=None,
        obs_longitude=None,
    )
    assert matches(spec, inputs) is False


def test_not_within_radius_matches_when_all_priors_far_enough() -> None:
    spec = MatchNotWithinRadius(kind="not_within_radius_of_existing", radius_meters=50)
    # ~110m away (1/1000 of a degree latitude ~= 111m)
    far = PriorObservation(latitude=39.101, longitude=-84.5)
    assert matches(spec, _inputs(lat=39.1, lng=-84.5, priors=(far,))) is True


def test_not_within_radius_no_match_when_a_prior_is_too_close() -> None:
    spec = MatchNotWithinRadius(kind="not_within_radius_of_existing", radius_meters=50)
    near = PriorObservation(latitude=39.1, longitude=-84.5)  # same point
    assert matches(spec, _inputs(lat=39.1, lng=-84.5, priors=(near,))) is False


# ---------------------------------------------------------------------------
# taxon_set
# ---------------------------------------------------------------------------


def test_taxon_set_matches_exact_taxon() -> None:
    spec = MatchTaxonSet(kind="taxon_set", value="pollinators")
    inputs = _inputs(
        taxon=_bird_taxon(47157),
        taxon_sets={"pollinators": frozenset({47157})},
    )
    assert matches(spec, inputs) is True


def test_taxon_set_matches_descendant_via_ancestor_chain() -> None:
    spec = MatchTaxonSet(kind="taxon_set", value="pollinators")
    taxon = TaxonInfo(taxon_id=999999, iconic_taxon="Insecta", ancestor_ids=(1, 47157, 123))
    inputs = _inputs(taxon=taxon, taxon_sets={"pollinators": frozenset({47157})})
    assert matches(spec, inputs) is True


def test_taxon_set_no_match_for_missing_set_or_taxon() -> None:
    spec = MatchTaxonSet(kind="taxon_set", value="pollinators")
    assert matches(spec, _inputs(taxon=_bird_taxon(12345))) is False
    assert (
        matches(spec, _inputs(taxon=None, taxon_sets={"pollinators": frozenset({47157})})) is False
    )


# ---------------------------------------------------------------------------
# not_in_current_expedition
# ---------------------------------------------------------------------------


def test_not_in_current_expedition_matches_new_taxon_for_this_run() -> None:
    spec = MatchNotInCurrentExpedition(kind="not_in_current_expedition")
    inputs = _inputs(taxon=_bird_taxon(12345), current_expedition_taxa=frozenset({999}))
    assert matches(spec, inputs) is True


def test_not_in_current_expedition_blocks_repeat_taxon_for_this_run() -> None:
    spec = MatchNotInCurrentExpedition(kind="not_in_current_expedition")
    inputs = _inputs(taxon=_bird_taxon(12345), current_expedition_taxa=frozenset({12345}))
    assert matches(spec, inputs) is False


# ---------------------------------------------------------------------------
# observation_tag
# ---------------------------------------------------------------------------


def test_observation_tag_matches_closed_choice_value() -> None:
    spec = MatchObservationTag(kind="observation_tag", key="life_stage", value="flower")
    assert matches(spec, _inputs(ecology_tags={"life_stage": "flower"})) is True


def test_observation_tag_no_match_for_missing_or_different_value() -> None:
    spec = MatchObservationTag(kind="observation_tag", key="life_stage", value="flower")
    assert matches(spec, _inputs(ecology_tags={})) is False
    assert matches(spec, _inputs(ecology_tags={"life_stage": "seedling"})) is False


# ---------------------------------------------------------------------------
# combinators
# ---------------------------------------------------------------------------


def test_all_of_requires_every_subspec_to_match() -> None:
    spec = MatchAllOf(
        kind="all_of",
        matches=[
            MatchIconicTaxon(kind="iconic_taxon", value="Aves"),
            MatchNotInDex(kind="not_in_dex"),
        ],
    )
    # Bird, not in Dex -> match
    assert matches(spec, _inputs(taxon=_bird_taxon(), dex=frozenset())) is True
    # Bird, IN Dex -> no match
    assert matches(spec, _inputs(taxon=_bird_taxon(12345), dex=frozenset({12345}))) is False


def test_any_of_short_circuits_on_first_match() -> None:
    spec = MatchAnyOf(
        kind="any_of",
        matches=[
            MatchIconicTaxon(kind="iconic_taxon", value="Plantae"),
            MatchIconicTaxon(kind="iconic_taxon", value="Aves"),
        ],
    )
    # Plantae no, Aves yes -> overall yes
    assert matches(spec, _inputs(taxon=_bird_taxon())) is True


def test_any_of_no_match_when_all_subspecs_fail() -> None:
    spec = MatchAnyOf(
        kind="any_of",
        matches=[
            MatchIconicTaxon(kind="iconic_taxon", value="Plantae"),
            MatchIconicTaxon(kind="iconic_taxon", value="Mammalia"),
        ],
    )
    assert matches(spec, _inputs(taxon=_bird_taxon())) is False


def test_combinators_can_nest_two_levels() -> None:
    """all_of(any_of(plantae, fungi), not_in_dex)"""
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
    plant = TaxonInfo(taxon_id=99, iconic_taxon="Plantae", ancestor_ids=())
    assert matches(spec, _inputs(taxon=plant, dex=frozenset())) is True
    assert (
        matches(spec, _inputs(taxon=_bird_taxon(), dex=frozenset())) is False
    )  # not in plant/fungi
