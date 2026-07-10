"""Unit tests for app.inat.submit (the two-call iNat dance)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
import respx

from app.core.config import Settings
from app.inat.client import InatUnavailable, build_inat_client
from app.inat.submit import submit_observation_to_inat as _submit_observation_to_inat


@pytest.fixture
def settings() -> Settings:
    return Settings(env="local", inat_oauth_token="test-token")


@pytest.fixture
def client(settings: Settings) -> httpx.AsyncClient:
    return build_inat_client(settings)


_HINTERLAND_OBS_ID = "01J0OBSID00000000000000ULID"
_OBSERVED_ON = datetime(2026, 5, 10, 12, 0, 0, tzinfo=UTC)


async def submit_observation_to_inat(*args: Any, **kwargs: Any) -> Any:
    kwargs["egress_enabled"] = True
    return await _submit_observation_to_inat(*args, **kwargs)


@respx.mock
async def test_kill_switch_blocks_before_first_http_call(client: httpx.AsyncClient) -> None:
    route = respx.post("https://api.inaturalist.org/v1/observations").mock(
        return_value=httpx.Response(200, json={"id": 1})
    )
    with pytest.raises(InatUnavailable, match="egress is disabled"):
        await _submit_observation_to_inat(
            client,
            hinterland_observation_id=_HINTERLAND_OBS_ID,
            photo_bytes=b"jpeg",
            latitude=39.1,
            longitude=-84.5,
            observed_on=_OBSERVED_ON,
        )
    assert route.called is False


@respx.mock
async def test_happy_path(client: httpx.AsyncClient) -> None:
    obs_route = respx.post("https://api.inaturalist.org/v1/observations").mock(
        return_value=httpx.Response(200, json={"id": 9876543, "uuid": _HINTERLAND_OBS_ID})
    )
    photo_route = respx.post("https://api.inaturalist.org/v1/observation_photos").mock(
        return_value=httpx.Response(200, json={"id": 12345})
    )

    result = await submit_observation_to_inat(
        client,
        hinterland_observation_id=_HINTERLAND_OBS_ID,
        photo_bytes=b"jpeg",
        latitude=39.1,
        longitude=-84.5,
        observed_on=_OBSERVED_ON,
        taxon_id=12345,
        species_guess="Northern Cardinal",
    )
    assert result.inat_observation_id == 9876543
    assert result.inat_uuid == _HINTERLAND_OBS_ID
    assert obs_route.called
    assert photo_route.called


@respx.mock
async def test_payload_rounds_coords_and_sets_geoprivacy(client: httpx.AsyncClient) -> None:
    """Child-location privacy posture: coordinates leave our system
    rounded to ~1.1 km AND flagged geoprivacy=obscured. Pin the exact
    JSON so neither layer can silently regress."""
    obs_route = respx.post("https://api.inaturalist.org/v1/observations").mock(
        return_value=httpx.Response(200, json={"id": 1, "uuid": _HINTERLAND_OBS_ID})
    )
    respx.post("https://api.inaturalist.org/v1/observation_photos").mock(
        return_value=httpx.Response(200, json={"id": 2})
    )

    await submit_observation_to_inat(
        client,
        hinterland_observation_id=_HINTERLAND_OBS_ID,
        photo_bytes=b"jpeg",
        latitude=39.123456,
        longitude=-84.567891,
        observed_on=_OBSERVED_ON,
        taxon_id=12345,
    )

    body = json.loads(obs_route.calls[0].request.content)
    observation = body["observation"]
    assert observation["latitude"] == 39.12
    assert observation["longitude"] == -84.57
    assert observation["geoprivacy"] == "obscured"
    # Full-precision coordinates never appear anywhere in the payload.
    assert "39.123456" not in obs_route.calls[0].request.content.decode()


@respx.mock
async def test_handles_results_wrapped_response(client: httpx.AsyncClient) -> None:
    """iNat sometimes wraps the create response as {results: [...]}."""
    respx.post("https://api.inaturalist.org/v1/observations").mock(
        return_value=httpx.Response(
            200,
            json={"results": [{"id": 4242, "uuid": _HINTERLAND_OBS_ID}]},
        )
    )
    respx.post("https://api.inaturalist.org/v1/observation_photos").mock(
        return_value=httpx.Response(200, json={"id": 1})
    )
    result = await submit_observation_to_inat(
        client,
        hinterland_observation_id=_HINTERLAND_OBS_ID,
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
            hinterland_observation_id=_HINTERLAND_OBS_ID,
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
            hinterland_observation_id=_HINTERLAND_OBS_ID,
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
            hinterland_observation_id=_HINTERLAND_OBS_ID,
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
            hinterland_observation_id=_HINTERLAND_OBS_ID,
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
            hinterland_observation_id=_HINTERLAND_OBS_ID,
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
            hinterland_observation_id=_HINTERLAND_OBS_ID,
            photo_bytes=b"jpeg",
            latitude=39.1,
            longitude=-84.5,
            observed_on=_OBSERVED_ON,
        )
