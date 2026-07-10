"""Privacy-preserving reverse geocoding from a coarse geohash body."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, field_validator

from app.core.auth import CurrentUserDep
from app.core.geospatial import decode_geohash_exactly, normalize_geohash4
from app.db.session import DbSessionDep
from app.geocoding.cache import reverse_with_cache
from app.geocoding.provider import GeocoderDep

router = APIRouter(prefix="/v1/geocode", tags=["geocode"])


class ReverseGeocodeRequest(BaseModel):
    geohash4: str

    @field_validator("geohash4")
    @classmethod
    def validate_geohash4(cls, value: str) -> str:
        return normalize_geohash4(value)


class ReverseGeocodeResponse(BaseModel):
    geohash4: str
    place_name: str | None


@router.post("/reverse", response_model=ReverseGeocodeResponse)
async def reverse_geocode(
    payload: ReverseGeocodeRequest,
    current_user: CurrentUserDep,
    session: DbSessionDep,
    geocoder: GeocoderDep,
) -> ReverseGeocodeResponse:
    del current_user
    lat, lng, _, _ = decode_geohash_exactly(payload.geohash4)
    place_name = await reverse_with_cache(session, geocoder, lat=lat, lng=lng)
    return ReverseGeocodeResponse(geohash4=payload.geohash4, place_name=place_name)
