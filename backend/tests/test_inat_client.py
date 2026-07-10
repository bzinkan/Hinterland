"""Unit tests for the iNat client wrappers (cv + taxa + observations).

Mocks the iNat HTTP surface with respx so no real network call happens.
Verifies the graceful-degradation contract from `docs/architecture.md`:
transient iNat outages raise InatUnavailable; client errors return empty
results without raising.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx

from app.core.config import Settings
from app.inat.client import InatUnavailable, build_inat_client
from app.inat.cv import score_image as _score_image
from app.inat.observations import SpeciesCount, get_species_counts
from app.inat.taxa import get_taxon


@pytest.fixture
def settings() -> Settings:
    return Settings(env="local", inat_oauth_token="test-token-not-real")


@pytest.fixture
def client(settings: Settings) -> httpx.AsyncClient:
    return build_inat_client(settings)


# ---------------------------------------------------------------------------
# score_image
# ---------------------------------------------------------------------------


async def score_image(*args: Any, **kwargs: Any) -> Any:
    kwargs["egress_enabled"] = True
    return await _score_image(*args, **kwargs)


@respx.mock
async def test_score_image_kill_switch_blocks_before_http(client: httpx.AsyncClient) -> None:
    route = respx.post("https://api.inaturalist.org/v1/computervision/score_image").mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    with pytest.raises(InatUnavailable, match="egress is disabled"):
        await _score_image(client, image_bytes=b"child-photo")
    assert route.called is False


@respx.mock
async def test_score_image_returns_top_k_suggestions(client: httpx.AsyncClient) -> None:
    respx.post("https://api.inaturalist.org/v1/computervision/score_image").mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {
                        "combined_score": 92.5,
                        "taxon": {
                            "id": 12345,
                            "name": "Cardinalis cardinalis",
                            "preferred_common_name": "Northern Cardinal",
                        },
                    },
                    {
                        "combined_score": 51.0,
                        "taxon": {
                            "id": 67890,
                            "name": "Cardinalis sinuatus",
                            "preferred_common_name": "Pyrrhuloxia",
                        },
                    },
                    {
                        "combined_score": 12.0,
                        "taxon": {"id": 11, "name": "Cardinalidae"},
                    },
                ]
            },
        )
    )

    suggestions = await score_image(client, image_bytes=b"fake-jpeg", top_k=3)

    assert len(suggestions) == 3
    assert suggestions[0].taxon_id == 12345
    assert suggestions[0].common_name == "Northern Cardinal"
    assert suggestions[0].scientific_name == "Cardinalis cardinalis"
    assert suggestions[0].score == 92.5
    assert suggestions[2].common_name == "Cardinalidae"  # falls back to scientific


@respx.mock
async def test_score_image_truncates_to_top_k(client: httpx.AsyncClient) -> None:
    respx.post("https://api.inaturalist.org/v1/computervision/score_image").mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [{"score": 90, "taxon": {"id": i, "name": f"sp{i}"}} for i in range(10)]
            },
        )
    )
    suggestions = await score_image(client, image_bytes=b"x", top_k=3)
    assert len(suggestions) == 3
    assert [s.taxon_id for s in suggestions] == [0, 1, 2]


@respx.mock
async def test_score_image_returns_empty_on_4xx(client: httpx.AsyncClient) -> None:
    respx.post("https://api.inaturalist.org/v1/computervision/score_image").mock(
        return_value=httpx.Response(400, json={"error": "bad image"})
    )
    suggestions = await score_image(client, image_bytes=b"x")
    assert suggestions == []


@respx.mock
async def test_score_image_raises_on_5xx(client: httpx.AsyncClient) -> None:
    respx.post("https://api.inaturalist.org/v1/computervision/score_image").mock(
        return_value=httpx.Response(503)
    )
    with pytest.raises(InatUnavailable):
        await score_image(client, image_bytes=b"x")


@respx.mock
async def test_score_image_raises_on_unauthorized(client: httpx.AsyncClient) -> None:
    respx.post("https://api.inaturalist.org/v1/computervision/score_image").mock(
        return_value=httpx.Response(401)
    )
    with pytest.raises(InatUnavailable):
        await score_image(client, image_bytes=b"x")


@respx.mock
async def test_score_image_raises_on_transport_error(client: httpx.AsyncClient) -> None:
    respx.post("https://api.inaturalist.org/v1/computervision/score_image").mock(
        side_effect=httpx.ConnectError("network down")
    )
    with pytest.raises(InatUnavailable):
        await score_image(client, image_bytes=b"x")


@respx.mock
async def test_score_image_handles_empty_results(client: httpx.AsyncClient) -> None:
    respx.post("https://api.inaturalist.org/v1/computervision/score_image").mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    suggestions = await score_image(client, image_bytes=b"x")
    assert suggestions == []


# ---------------------------------------------------------------------------
# get_taxon
# ---------------------------------------------------------------------------


@respx.mock
async def test_get_taxon_happy(client: httpx.AsyncClient) -> None:
    respx.get("https://api.inaturalist.org/v1/taxa/12345").mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {
                        "id": 12345,
                        "name": "Cardinalis cardinalis",
                        "preferred_common_name": "Northern Cardinal",
                        "iconic_taxon_name": "Aves",
                    }
                ]
            },
        )
    )
    info = await get_taxon(client, 12345)
    assert info is not None
    assert info.scientific_name == "Cardinalis cardinalis"
    assert info.common_name == "Northern Cardinal"
    assert info.iconic_taxon == "Aves"


@respx.mock
async def test_get_taxon_returns_none_on_404(client: httpx.AsyncClient) -> None:
    respx.get("https://api.inaturalist.org/v1/taxa/99999").mock(return_value=httpx.Response(404))
    assert await get_taxon(client, 99999) is None


@respx.mock
async def test_get_taxon_raises_on_5xx(client: httpx.AsyncClient) -> None:
    respx.get("https://api.inaturalist.org/v1/taxa/1").mock(return_value=httpx.Response(502))
    with pytest.raises(InatUnavailable):
        await get_taxon(client, 1)


@respx.mock
async def test_get_taxon_returns_none_on_empty_results(client: httpx.AsyncClient) -> None:
    respx.get("https://api.inaturalist.org/v1/taxa/1").mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    assert await get_taxon(client, 1) is None


@respx.mock
@pytest.mark.parametrize("status", [401, 403, 429])
async def test_get_taxon_raises_on_auth_or_rate_limit(
    client: httpx.AsyncClient, status: int
) -> None:
    """Auth/rate problems are OUR outage, not the taxon's absence --
    callers must degrade (facts_available=false, species_name as-is)
    rather than treat the taxon as nonexistent."""
    respx.get("https://api.inaturalist.org/v1/taxa/1").mock(return_value=httpx.Response(status))
    with pytest.raises(InatUnavailable):
        await get_taxon(client, 1)


# ---------------------------------------------------------------------------
# get_species_counts
# ---------------------------------------------------------------------------


async def _species_counts(client: httpx.AsyncClient) -> list[SpeciesCount]:
    return await get_species_counts(
        client,
        bbox_swlat=39.0,
        bbox_swlng=-84.6,
        bbox_nelat=39.2,
        bbox_nelng=-84.4,
    )


@respx.mock
async def test_get_species_counts_keeps_iconic_taxon_name(client: httpx.AsyncClient) -> None:
    respx.get("https://api.inaturalist.org/v1/observations/species_counts").mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {"taxon": {"id": 1, "iconic_taxon_name": "Aves"}, "count": 60},
                    {"taxon": {"id": 2, "iconic_taxon_name": "Insecta"}, "count": 30},
                ]
            },
        )
    )
    counts = await _species_counts(client)
    assert [(c.taxon_id, c.count, c.iconic_taxon_name) for c in counts] == [
        (1, 60, "Aves"),
        (2, 30, "Insecta"),
    ]


@respx.mock
async def test_get_species_counts_iconic_taxon_none_when_missing_or_non_str(
    client: httpx.AsyncClient,
) -> None:
    respx.get("https://api.inaturalist.org/v1/observations/species_counts").mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {"taxon": {"id": 1}, "count": 10},  # missing
                    {"taxon": {"id": 2, "iconic_taxon_name": 42}, "count": 20},  # non-str
                    {"taxon": {"id": 3, "iconic_taxon_name": None}, "count": 30},  # null
                    {"taxon": {"id": 4, "iconic_taxon_name": ""}, "count": 40},  # empty
                ]
            },
        )
    )
    counts = await _species_counts(client)
    assert [c.iconic_taxon_name for c in counts] == [None, None, None, None]
