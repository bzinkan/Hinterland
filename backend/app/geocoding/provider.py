"""Pluggable reverse-geocoding providers.

Two impls today:

- `NoOpGeocoder` -- returns None for every lookup. Used in tests and
  whenever a real provider isn't configured. The kid never sees a
  blocking error -- they just don't get a place_name.
- `NominatimGeocoder` -- hits the public Nominatim instance. Free, but
  rate-limited to 1 req/sec and forbidden from commercial use. Fine for
  dev / staging; production needs a contracted provider (Google Maps
  Geocoding API, self-hosted Nominatim, etc.) per docs/runbook.md.
"""

from __future__ import annotations

from typing import Annotated, Protocol, cast

import httpx
import structlog
from fastapi import Depends, Request

from app.core.config import Settings

log = structlog.get_logger()


class Geocoder(Protocol):
    async def reverse(self, *, lat: float, lng: float) -> str | None:
        """Return a human place name (e.g. "Cincinnati, OH"), or None."""
        ...


class NoOpGeocoder:
    async def reverse(self, *, lat: float, lng: float) -> str | None:
        return None


class NominatimGeocoder:
    def __init__(self, base_url: str, user_agent: str, timeout: float) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout,
            headers={"User-Agent": user_agent, "Accept": "application/json"},
        )

    async def reverse(self, *, lat: float, lng: float) -> str | None:
        try:
            res = await self._client.get(
                "/reverse",
                params={
                    "lat": lat,
                    "lon": lng,
                    "format": "jsonv2",
                    "zoom": 10,  # ~city level
                    "addressdetails": 0,
                },
            )
        except (httpx.TransportError, httpx.TimeoutException) as exc:
            log.warning("geocoding.nominatim.transport_error", error=str(exc))
            return None

        if res.status_code != 200:
            log.warning(
                "geocoding.nominatim.non_200",
                status=res.status_code,
                body=res.text[:200],
            )
            return None

        payload = cast(dict[str, object], res.json())
        display = payload.get("display_name")
        if isinstance(display, str) and display:
            return display
        return None


def build_geocoder(settings: Settings) -> Geocoder:
    if settings.geocoding_provider == "nominatim":
        return NominatimGeocoder(
            base_url=settings.geocoding_nominatim_base_url,
            user_agent=settings.geocoding_user_agent,
            timeout=settings.geocoding_request_timeout_seconds,
        )
    return NoOpGeocoder()


def get_geocoder(request: Request) -> Geocoder:
    geocoder = getattr(request.app.state, "geocoder", None)
    if geocoder is None:
        settings: Settings = request.app.state.settings
        geocoder = build_geocoder(settings)
        request.app.state.geocoder = geocoder
    return cast(Geocoder, geocoder)


GeocoderDep = Annotated[Geocoder, Depends(get_geocoder)]
