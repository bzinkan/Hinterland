"""Tiny pure-Python helpers for privacy-preserving coarse geohashes.

The observation hot path only needs encoding at precision four. Keeping that
operation here avoids a compiled/abandoned dependency on Windows builders and
makes the allowed alphabet explicit at the API boundary.
"""

from __future__ import annotations

import math
import re

_BASE32 = "0123456789bcdefghjkmnpqrstuvwxyz"
_DECODE = {character: index for index, character in enumerate(_BASE32)}
_GEOHASH4 = re.compile(r"^[0-9bcdefghjkmnpqrstuvwxyz]{4}$")


def encode_geohash(latitude: float, longitude: float, *, precision: int = 4) -> str:
    if not math.isfinite(latitude) or not -90 <= latitude <= 90:
        raise ValueError("latitude must be finite and between -90 and 90")
    if not math.isfinite(longitude) or not -180 <= longitude <= 180:
        raise ValueError("longitude must be finite and between -180 and 180")
    if not 1 <= precision <= 12:
        raise ValueError("precision must be between 1 and 12")

    lat_range = [-90.0, 90.0]
    lng_range = [-180.0, 180.0]
    even_bit = True
    bit = 0
    value = 0
    encoded: list[str] = []

    while len(encoded) < precision:
        bounds = lng_range if even_bit else lat_range
        coordinate = longitude if even_bit else latitude
        midpoint = (bounds[0] + bounds[1]) / 2
        if coordinate >= midpoint:
            value |= 1 << (4 - bit)
            bounds[0] = midpoint
        else:
            bounds[1] = midpoint
        even_bit = not even_bit
        if bit < 4:
            bit += 1
        else:
            encoded.append(_BASE32[value])
            bit = 0
            value = 0
    return "".join(encoded)


def normalize_geohash4(value: str) -> str:
    normalized = value.strip().lower()
    if not _GEOHASH4.fullmatch(normalized):
        raise ValueError("geohash4 must contain exactly four geohash characters")
    return normalized


def decode_geohash_exactly(value: str) -> tuple[float, float, float, float]:
    """Return latitude, longitude, latitude error, and longitude error."""
    normalized = value.strip().lower()
    if not normalized or any(character not in _DECODE for character in normalized):
        raise ValueError("invalid geohash")
    lat_range = [-90.0, 90.0]
    lng_range = [-180.0, 180.0]
    even_bit = True
    for character in normalized:
        encoded = _DECODE[character]
        for mask in (16, 8, 4, 2, 1):
            bounds = lng_range if even_bit else lat_range
            midpoint = (bounds[0] + bounds[1]) / 2
            if encoded & mask:
                bounds[0] = midpoint
            else:
                bounds[1] = midpoint
            even_bit = not even_bit
    latitude = (lat_range[0] + lat_range[1]) / 2
    longitude = (lng_range[0] + lng_range[1]) / 2
    return (
        latitude,
        longitude,
        (lat_range[1] - lat_range[0]) / 2,
        (lng_range[1] - lng_range[0]) / 2,
    )
