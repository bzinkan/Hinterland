"""Reverse geocoding utility endpoint.

`GET /v1/geocode/reverse?lat=..&lng=..` -> `{place_name: "..." | null}`.

Pure utility -- the mobile client decides when to ask. The companion
PATCH /v1/observations/{id} endpoint is what actually persists the
result on an observation row. Splitting them keeps geocoding cacheable
across observations (two kids in the same neighborhood share the cache
hit) and keeps the observation routes from coupling to the geocoder.
"""

from __future__ import annotations

from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.core.auth import CurrentUserDep
from app.db.session import DbSessionDep
from app.geocoding.cache import reverse_with_cache
from app.geocoding.provider import GeocoderDep

router = APIRouter(prefix="/v1/geocode", tags=["geocode"])


class ReverseGeocodeResponse(BaseModel):
    lat: float
    lng: float
    place_name: str | None


@router.get("/reverse", response_model=ReverseGeocodeResponse)
async def reverse_geocode(
    current_user: CurrentUserDep,
    session: DbSessionDep,
    geocoder: GeocoderDep,
    lat: float = Query(..., ge=-90.0, le=90.0),
    lng: float = Query(..., ge=-180.0, le=180.0),
) -> ReverseGeocodeResponse:
    place_name = await reverse_with_cache(session, geocoder, lat=lat, lng=lng)
    return ReverseGeocodeResponse(lat=lat, lng=lng, place_name=place_name)
