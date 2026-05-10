"""Unit tests for app.inat.submit (the two-call iNat dance)."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest
import respx

from app.core.config import Settings
from app.inat.client import InatUnavailable, build_inat_client
from app.inat.submit import submit_observation_to_inat


@pytest.fixture
def settings() -> Settings:
    return Settings(env="local", inat_oauth_token="test-token")


@pytest.fixture
def client(settings: Settings) -> httpx.AsyncClient:
    return build_inat_client(settings)


_DRAGONFLY_OBS_ID = "01J0OBSID00000000000000ULID"
_OBSERVED_ON = datetime(2026, 5, 10, 12, 0, 0, tzinfo=UTC)


@respx.mock
async def test_happy_path(client: httpx.AsyncClient) -> None:
    obs_route = respx.post("https://api.inaturalist.org/v1/observations").mock(
        return_value=httpx.Response(200, json={"id": 9876543, "uuid": _DRAGONFLY_OBS_ID})
    )
    photo_route = respx.post("https://api.inaturalist.org/v1/observation_photos").mock(
        return_value=httpx.Response(200, json={"id": 12345})
    )

    result = await submit_observation_to_inat(
        client,
        dragonfly_observation_id=_DRAGONFLY_OBS_ID,
        photo_bytes=b"jpeg",
        latitude=39.1,
        longitude=-84.5,
        observed_on=_OBSERVED_ON,
        taxon_id=12345,
        species_guess="Northern Cardinal",
    )
    assert result.inat_observation_id == 9876543
    assert result.inat_uuid == _DRAGONFLY_OBS_ID
    assert obs_route.called
    assert photo_route.called


@respx.mock
async def test_handles_results_wrapped_response(client: httpx.AsyncClient) -> None:
    """iNat sometimes wraps the create response as {results: [...]}."""
    respx.post("https://api.inaturalist.org/v1/observations").mock(
        return_value=httpx.Response(
            200,
            json={"results": [{"id": 4242, "uuid": _DRAGONFLY_OBS_ID}]},
        )
    )
    respx.post("https://api.inaturalist.org/v1/observation_photos").mock(
        return_value=httpx.Response(200, json={"id": 1})
    )
    result = await submit_observation_to_inat(
        client,
        dragonfly_observation_id=_DRAGONFLY_OBS_ID,
        photo_bytes=b"jpeg",
        latitude=39.1,
        longitude=-84.5,
        observed_on=_OBSERVED_ON,
    )
    assert result.inat_observation_id == 4242


@respx.mock
async def test_observation_create_5xx_raises_unavailable(client: httpx.AsyncClient) -> None:
    respx.post("https://api.inaturalist.org/v1/observations").mock(return_value=httpx.Response(503))
    with pytest.raises(InatUnavailable):
        await submit_observation_to_inat(
            client,
            dragonfly_observation_id=_DRAGONFLY_OBS_ID,
            photo_bytes=b"jpeg",
            latitude=39.1,
            longitude=-84.5,
            observed_on=_OBSERVED_ON,
        )


@respx.mock
async def test_observation_create_401_raises_unavailable(client: httpx.AsyncClient) -> None:
    respx.post("https://api.inaturalist.org/v1/observations").mock(return_value=httpx.Response(401))
    with pytest.raises(InatUnavailable):
        await submit_observation_to_inat(
            client,
            dragonfly_observation_id=_DRAGONFLY_OBS_ID,
            photo_bytes=b"jpeg",
            latitude=39.1,
            longitude=-84.5,
            observed_on=_OBSERVED_ON,
        )


@respx.mock
async def test_observation_create_4xx_raises_unavailable(client: httpx.AsyncClient) -> None:
    """4xx other than auth -- still treat as transient so Cloud Tasks retries.
    Genuinely malformed requests will exhaust retries and DLQ."""
    respx.post("https://api.inaturalist.org/v1/observations").mock(
        return_value=httpx.Response(422, json={"error": "bad"})
    )
    with pytest.raises(InatUnavailable):
        await submit_observation_to_inat(
            client,
            dragonfly_observation_id=_DRAGONFLY_OBS_ID,
            photo_bytes=b"jpeg",
            latitude=39.1,
            longitude=-84.5,
            observed_on=_OBSERVED_ON,
        )


@respx.mock
async def test_observation_create_transport_error(client: httpx.AsyncClient) -> None:
    respx.post("https://api.inaturalist.org/v1/observations").mock(
        side_effect=httpx.ConnectError("network down")
    )
    with pytest.raises(InatUnavailable):
        await submit_observation_to_inat(
            client,
            dragonfly_observation_id=_DRAGONFLY_OBS_ID,
            photo_bytes=b"jpeg",
            latitude=39.1,
            longitude=-84.5,
            observed_on=_OBSERVED_ON,
        )


@respx.mock
async def test_photo_upload_5xx_raises_unavailable(client: httpx.AsyncClient) -> None:
    respx.post("https://api.inaturalist.org/v1/observations").mock(
        return_value=httpx.Response(200, json={"id": 9876543})
    )
    respx.post("https://api.inaturalist.org/v1/observation_photos").mock(
        return_value=httpx.Response(502)
    )
    with pytest.raises(InatUnavailable):
        await submit_observation_to_inat(
            client,
            dragonfly_observation_id=_DRAGONFLY_OBS_ID,
            photo_bytes=b"jpeg",
            latitude=39.1,
            longitude=-84.5,
            observed_on=_OBSERVED_ON,
        )


@respx.mock
async def test_observation_create_no_id_in_response_raises(client: httpx.AsyncClient) -> None:
    respx.post("https://api.inaturalist.org/v1/observations").mock(
        return_value=httpx.Response(200, json={"weird": "shape"})
    )
    with pytest.raises(InatUnavailable):
        await submit_observation_to_inat(
            client,
            dragonfly_observation_id=_DRAGONFLY_OBS_ID,
            photo_bytes=b"jpeg",
            latitude=39.1,
            longitude=-84.5,
            observed_on=_OBSERVED_ON,
        )
