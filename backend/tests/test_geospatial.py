import pytest

from app.core.geospatial import encode_geohash, normalize_geohash4


def test_encode_geohash_matches_known_cincinnati_cell() -> None:
    assert encode_geohash(39.1031, -84.5120, precision=4) == "dngy"


def test_normalize_geohash4_rejects_ambiguous_characters() -> None:
    assert normalize_geohash4(" DNGY ") == "dngy"
    with pytest.raises(ValueError):
        normalize_geohash4("dnio")
