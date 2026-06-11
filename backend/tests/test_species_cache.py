"""Tests for the species_cache read-through helper."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import respx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.db import models
from app.inat.client import build_inat_client
from app.services import species_cache


@pytest.fixture
def settings() -> Settings:
    return Settings(env="local")


@pytest.fixture
def inat_client(settings: Settings) -> httpx.AsyncClient:
    return build_inat_client(settings)


@pytest.fixture
def fake_session() -> AsyncMock:
    return AsyncMock(spec=AsyncSession)


def _wire_cache_lookup(fake_session: AsyncMock, *, hit: models.SpeciesCache | None) -> None:
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=hit)
    fake_session.execute = AsyncMock(return_value=result)
    fake_session.add = MagicMock()
    fake_session.flush = AsyncMock()
    fake_session.rollback = AsyncMock()


async def test_cache_hit_skips_inat_call(fake_session: AsyncMock) -> None:
    cached_row = models.SpeciesCache(
        taxon_id=12345,
        scientific_name="Cardinalis cardinalis",
        common_name="Northern Cardinal",
        iconic_taxon="Aves",
        source_payload={},
    )
    _wire_cache_lookup(fake_session, hit=cached_row)
    # If the iNat client is touched, the test will fail noisily because
    # we passed a real client and respx isn't installed.
    inat_client_unused = httpx.AsyncClient(base_url="http://unused.invalid")

    species = await species_cache.get_or_fill(fake_session, inat_client_unused, 12345)

    assert species is not None
    assert species.taxon_id == 12345
    assert species.common_name == "Northern Cardinal"
    fake_session.add.assert_not_called()


@respx.mock
async def test_cache_miss_fetches_and_writes(
    fake_session: AsyncMock, inat_client: httpx.AsyncClient
) -> None:
    _wire_cache_lookup(fake_session, hit=None)
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
                        "ancestor_ids": [48460, 1, 3, 12345],
                    }
                ]
            },
        )
    )

    species = await species_cache.get_or_fill(fake_session, inat_client, 12345)

    assert species is not None
    assert species.scientific_name == "Cardinalis cardinalis"
    # Pulled from the raw payload; the taxon's own trailing id is excluded.
    assert species.ancestor_ids == (48460, 1, 3)
    fake_session.add.assert_called_once()
    written: models.SpeciesCache = fake_session.add.call_args.args[0]
    assert isinstance(written, models.SpeciesCache)
    assert written.taxon_id == 12345
    assert written.common_name == "Northern Cardinal"
    fake_session.flush.assert_awaited_once()


@respx.mock
async def test_cache_miss_unknown_taxon_returns_none(
    fake_session: AsyncMock, inat_client: httpx.AsyncClient
) -> None:
    _wire_cache_lookup(fake_session, hit=None)
    respx.get("https://api.inaturalist.org/v1/taxa/99999").mock(return_value=httpx.Response(404))

    species = await species_cache.get_or_fill(fake_session, inat_client, 99999)
    assert species is None
    fake_session.add.assert_not_called()


async def test_cache_hit_returns_ancestor_ids_from_payload(fake_session: AsyncMock) -> None:
    cached_row = models.SpeciesCache(
        taxon_id=12345,
        scientific_name="Cardinalis cardinalis",
        common_name="Northern Cardinal",
        iconic_taxon="Aves",
        source_payload={"ancestor_ids": [48460, 1, 3, 12345]},
    )
    _wire_cache_lookup(fake_session, hit=cached_row)
    inat_client_unused = httpx.AsyncClient(base_url="http://unused.invalid")

    species = await species_cache.get_or_fill(fake_session, inat_client_unused, 12345)

    assert species is not None
    assert species.ancestor_ids == (48460, 1, 3)


# ---------------------------------------------------------------------------
# ancestor_ids_from_payload
# ---------------------------------------------------------------------------


def test_ancestor_ids_missing_key_returns_empty() -> None:
    assert species_cache.ancestor_ids_from_payload({}, taxon_id=12345) == ()


def test_ancestor_ids_non_dict_payload_returns_empty() -> None:
    assert species_cache.ancestor_ids_from_payload(None, taxon_id=12345) == ()
    assert species_cache.ancestor_ids_from_payload("48460,1,3", taxon_id=12345) == ()


def test_ancestor_ids_non_list_value_returns_empty() -> None:
    payload = {"ancestor_ids": "48460,1,3"}
    assert species_cache.ancestor_ids_from_payload(payload, taxon_id=12345) == ()


def test_ancestor_ids_drops_bools_and_non_ints() -> None:
    payload = {"ancestor_ids": [48460, True, "1", 3.0, 3]}
    assert species_cache.ancestor_ids_from_payload(payload, taxon_id=12345) == (48460, 3)


def test_ancestor_ids_excludes_the_taxon_own_id() -> None:
    payload = {"ancestor_ids": [48460, 1, 3, 12345]}
    assert species_cache.ancestor_ids_from_payload(payload, taxon_id=12345) == (48460, 1, 3)


def test_ancestor_ids_happy_path() -> None:
    payload = {"ancestor_ids": [48460, 1, 2, 3]}
    assert species_cache.ancestor_ids_from_payload(payload, taxon_id=12345) == (48460, 1, 2, 3)
