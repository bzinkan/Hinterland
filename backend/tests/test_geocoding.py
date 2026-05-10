"""Tests for the geocoding provider, cache, and the /reverse endpoint."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

import app.core.auth as auth_module
from app.core.config import Settings
from app.db import models
from app.db.session import get_db_session
from app.geocoding.cache import reverse_with_cache
from app.geocoding.provider import (
    NominatimGeocoder,
    NoOpGeocoder,
    build_geocoder,
)
from app.main import create_app

_FIREBASE_UID = "firebase-kid-001"


# ---------------------------------------------------------------------------
# Provider unit tests
# ---------------------------------------------------------------------------


async def test_noop_geocoder_returns_none() -> None:
    geocoder = NoOpGeocoder()
    assert await geocoder.reverse(lat=39.1, lng=-84.5) is None


def test_build_geocoder_default_is_noop() -> None:
    geocoder = build_geocoder(Settings(env="local"))
    assert isinstance(geocoder, NoOpGeocoder)


def test_build_geocoder_nominatim() -> None:
    geocoder = build_geocoder(Settings(env="local", geocoding_provider="nominatim"))
    assert isinstance(geocoder, NominatimGeocoder)


@respx.mock
async def test_nominatim_returns_display_name() -> None:
    respx.get("https://nominatim.openstreetmap.org/reverse").mock(
        return_value=httpx.Response(
            200,
            json={"display_name": "Cincinnati, Hamilton County, Ohio, USA"},
        )
    )
    geocoder = NominatimGeocoder(
        base_url="https://nominatim.openstreetmap.org",
        user_agent="DragonflyTest/0.0",
        timeout=5.0,
    )
    place = await geocoder.reverse(lat=39.1, lng=-84.5)
    assert place == "Cincinnati, Hamilton County, Ohio, USA"


@respx.mock
async def test_nominatim_returns_none_on_5xx() -> None:
    respx.get("https://nominatim.openstreetmap.org/reverse").mock(return_value=httpx.Response(503))
    geocoder = NominatimGeocoder(
        base_url="https://nominatim.openstreetmap.org",
        user_agent="DragonflyTest/0.0",
        timeout=5.0,
    )
    assert await geocoder.reverse(lat=39.1, lng=-84.5) is None


@respx.mock
async def test_nominatim_returns_none_on_transport_error() -> None:
    respx.get("https://nominatim.openstreetmap.org/reverse").mock(
        side_effect=httpx.ConnectError("network down")
    )
    geocoder = NominatimGeocoder(
        base_url="https://nominatim.openstreetmap.org",
        user_agent="DragonflyTest/0.0",
        timeout=5.0,
    )
    assert await geocoder.reverse(lat=39.1, lng=-84.5) is None


@respx.mock
async def test_nominatim_returns_none_when_payload_missing_display() -> None:
    respx.get("https://nominatim.openstreetmap.org/reverse").mock(
        return_value=httpx.Response(200, json={"licence": "ODbL"})
    )
    geocoder = NominatimGeocoder(
        base_url="https://nominatim.openstreetmap.org",
        user_agent="DragonflyTest/0.0",
        timeout=5.0,
    )
    assert await geocoder.reverse(lat=39.1, lng=-84.5) is None


# ---------------------------------------------------------------------------
# Cache unit tests
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_session() -> AsyncMock:
    return AsyncMock(spec=AsyncSession)


def _wire_cache(fake_session: AsyncMock, *, hit: models.GeoCache | None) -> None:
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=hit)
    fake_session.execute = AsyncMock(return_value=result)
    fake_session.add = MagicMock()
    fake_session.flush = AsyncMock()
    fake_session.rollback = AsyncMock()


class _StaticGeocoder:
    def __init__(self, value: str | None) -> None:
        self._value = value
        self.calls: int = 0

    async def reverse(self, *, lat: float, lng: float) -> str | None:
        self.calls += 1
        return self._value


async def test_cache_hit_skips_geocoder(fake_session: AsyncMock) -> None:
    cached = models.GeoCache(
        id="39.103,-84.512",
        rounded_lat="39.103",
        rounded_lng="-84.512",
        place_name="Cincinnati, OH",
        source_payload={},
    )
    _wire_cache(fake_session, hit=cached)
    geocoder = _StaticGeocoder("SHOULD-NOT-BE-CALLED")

    place = await reverse_with_cache(fake_session, geocoder, lat=39.1031, lng=-84.5120)
    assert place == "Cincinnati, OH"
    assert geocoder.calls == 0
    fake_session.add.assert_not_called()


async def test_cache_miss_calls_geocoder_and_writes_row(fake_session: AsyncMock) -> None:
    _wire_cache(fake_session, hit=None)
    geocoder = _StaticGeocoder("Cincinnati, OH")

    place = await reverse_with_cache(fake_session, geocoder, lat=39.1031, lng=-84.5120)
    assert place == "Cincinnati, OH"
    assert geocoder.calls == 1

    fake_session.add.assert_called_once()
    written: models.GeoCache = fake_session.add.call_args.args[0]
    assert isinstance(written, models.GeoCache)
    assert written.rounded_lat == "39.103"
    assert written.rounded_lng == "-84.512"
    assert written.place_name == "Cincinnati, OH"


async def test_cache_miss_geocoder_none_no_write(fake_session: AsyncMock) -> None:
    _wire_cache(fake_session, hit=None)
    geocoder = _StaticGeocoder(None)

    place = await reverse_with_cache(fake_session, geocoder, lat=39.1, lng=-84.5)
    assert place is None
    fake_session.add.assert_not_called()


# ---------------------------------------------------------------------------
# Endpoint integration test
# ---------------------------------------------------------------------------


def _stub_token_verifier(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_verify(token: str, settings: Settings) -> dict[str, object]:
        return {"uid": _FIREBASE_UID, "role": "kid"}

    monkeypatch.setattr(auth_module, "verify_firebase_id_token", fake_verify)


def _build_client(fake_session: AsyncMock, geocoder: _StaticGeocoder) -> Iterator[TestClient]:
    app = create_app(Settings(env="local", app_version="test"))
    app.state.geocoder = geocoder

    async def override() -> AsyncIterator[AsyncSession]:
        yield fake_session

    app.dependency_overrides[get_db_session] = override
    with TestClient(app, raise_server_exceptions=True) as test_client:
        yield test_client


def test_reverse_endpoint_requires_bearer(fake_session: AsyncMock) -> None:
    for client in _build_client(fake_session, _StaticGeocoder(None)):
        response = client.get("/v1/geocode/reverse?lat=39.1&lng=-84.5")
        assert response.status_code == 401


def test_reverse_endpoint_returns_place_name_on_cache_miss(
    monkeypatch: pytest.MonkeyPatch, fake_session: AsyncMock
) -> None:
    _stub_token_verifier(monkeypatch)
    _wire_cache(fake_session, hit=None)

    for client in _build_client(fake_session, _StaticGeocoder("Cincinnati, OH")):
        response = client.get(
            "/v1/geocode/reverse?lat=39.1&lng=-84.5",
            headers={"Authorization": "Bearer fake"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["place_name"] == "Cincinnati, OH"
        assert body["lat"] == 39.1
        assert body["lng"] == -84.5


def test_reverse_endpoint_validates_lat_range(
    monkeypatch: pytest.MonkeyPatch, fake_session: AsyncMock
) -> None:
    _stub_token_verifier(monkeypatch)

    for client in _build_client(fake_session, _StaticGeocoder(None)):
        response = client.get(
            "/v1/geocode/reverse?lat=99&lng=-84.5",
            headers={"Authorization": "Bearer fake"},
        )
        assert response.status_code == 422
