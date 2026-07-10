"""Read-through cache over the `geo_cache` Postgres table.

Round lat/lng to 3 decimals (~110m, neighborhood-scale) so two kids
standing 50m apart hit the same cache entry. The rounded pair is also
the row's primary key so concurrent writers can't double-insert.
"""

from __future__ import annotations

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import models
from app.geocoding.provider import Geocoder

log = structlog.get_logger()

_PRECISION = 3


def _round(value: float) -> str:
    return f"{value:.{_PRECISION}f}"


def _row_id(rounded_lat: str, rounded_lng: str) -> str:
    return f"{rounded_lat},{rounded_lng}"


async def reverse_with_cache(
    session: AsyncSession,
    geocoder: Geocoder,
    *,
    lat: float,
    lng: float,
) -> str | None:
    """Return the cached or freshly-fetched place name for `(lat, lng)`.

    Returns None when both the cache misses AND the geocoder returns None
    (provider is no-op, or upstream gave us nothing usable).
    """
    rounded_lat = _round(lat)
    rounded_lng = _round(lng)
    row_id = _row_id(rounded_lat, rounded_lng)

    cached = (
        await session.execute(select(models.GeoCache).where(models.GeoCache.id == row_id))
    ).scalar_one_or_none()
    if cached is not None:
        return cached.place_name

    place_name = await geocoder.reverse(lat=lat, lng=lng)
    if place_name is None:
        return None

    row = models.GeoCache(
        id=row_id,
        rounded_lat=rounded_lat,
        rounded_lng=rounded_lng,
        place_name=place_name,
        source_payload={},
    )
    session.add(row)
    try:
        await session.flush()
        await session.commit()
    except Exception:
        # Concurrent writer beat us. Roll back, re-read, return whatever
        # they wrote (which will be the same place_name modulo provider
        # nondeterminism).
        await session.rollback()
        cached = (
            await session.execute(select(models.GeoCache).where(models.GeoCache.id == row_id))
        ).scalar_one_or_none()
        if cached is None:
            log.warning("geo_cache.race_lost_then_missing", id=row_id)
            return place_name  # Fall back to the value we just fetched.
        return cached.place_name

    log.info("geo_cache.filled", id=row_id)
    return place_name
