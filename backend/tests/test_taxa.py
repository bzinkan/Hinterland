from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.db import models
from app.db.session import get_db_session
from app.main import create_app
from tests.helpers.auth import stub_token_verifier

_FIREBASE_UID = "firebase-kid-001"
_USER_ID = "01J0KIDID0000000000000ULID"


class _PackStorage:
    def generate_get_url(self, **_: object) -> tuple[str, datetime]:
        return "https://storage.test/core.json?sas=redacted", datetime.now(UTC) + timedelta(
            minutes=15
        )


@pytest.fixture
def fake_session() -> AsyncMock:
    return AsyncMock(spec=AsyncSession)


@pytest.fixture
def taxa_client(fake_session: AsyncMock) -> Iterator[TestClient]:
    app = create_app(Settings(env="local", app_version="test"))
    app.state.signed_url_generator = _PackStorage()

    async def override() -> AsyncIterator[AsyncSession]:
        yield fake_session

    app.dependency_overrides[get_db_session] = override
    with TestClient(app, raise_server_exceptions=True) as client:
        yield client


def _user() -> models.User:
    return models.User(
        id=_USER_ID,
        firebase_uid=_FIREBASE_UID,
        role="kid",
        display_name="Kid",
    )


def test_taxa_search_requires_authentication(taxa_client: TestClient) -> None:
    assert taxa_client.get("/v1/taxa/search?q=cardinal").status_code == 401


def test_taxa_search_returns_only_local_canonical_rows(
    monkeypatch: pytest.MonkeyPatch,
    taxa_client: TestClient,
    fake_session: AsyncMock,
) -> None:
    stub_token_verifier(
        monkeypatch,
        uid=_FIREBASE_UID,
        role="kid",
        group_id="01J0GROUPID00000000000ULID",
    )
    user_result = MagicMock()
    user_result.scalar_one_or_none = MagicMock(return_value=_user())
    search_result = MagicMock()
    search_result.scalars = MagicMock(
        return_value=[
            models.SpeciesCache(
                taxon_id=12345,
                scientific_name="Cardinalis cardinalis",
                common_name="Northern Cardinal",
                iconic_taxon="Aves",
                rank="species",
                ancestor_ids=[3, 7251],
                aliases=[],
                active=True,
                catalog_version="2026-07-core",
                source_payload={},
            )
        ]
    )
    fake_session.execute = AsyncMock(side_effect=[user_result, search_result])

    response = taxa_client.get(
        "/v1/taxa/search?q=cardinal",
        headers={"Authorization": "Bearer fake"},
    )
    assert response.status_code == 200
    assert response.json()["items"] == [
        {
            "taxon_id": 12345,
            "scientific_name": "Cardinalis cardinalis",
            "common_name": "Northern Cardinal",
            "iconic_taxon": "Aves",
            "rank": "species",
            "ancestor_ids": [3, 7251],
            "catalog_version": "2026-07-core",
        }
    ]


def test_taxa_search_rejects_one_character_query(
    monkeypatch: pytest.MonkeyPatch, taxa_client: TestClient
) -> None:
    stub_token_verifier(monkeypatch, uid=_FIREBASE_UID, role="kid", group_id=None)
    assert (
        taxa_client.get(
            "/v1/taxa/search?q=x",
            headers={"Authorization": "Bearer fake"},
        ).status_code
        == 422
    )


def test_taxa_pack_returns_versioned_checksum_manifest(
    monkeypatch: pytest.MonkeyPatch,
    taxa_client: TestClient,
    fake_session: AsyncMock,
) -> None:
    stub_token_verifier(
        monkeypatch,
        uid=_FIREBASE_UID,
        role="kid",
        group_id="01J0GROUPID00000000000ULID",
    )
    user_result = MagicMock()
    user_result.scalar_one_or_none = MagicMock(return_value=_user())
    pack_result = MagicMock()
    pack_result.scalar_one_or_none = MagicMock(
        return_value=models.TaxonomyPack(
            id="01J0PACK000000000000000ULID",
            pack_id="core",
            version="2026.07.09.1",
            scope="global_core",
            checksum_sha256="a" * 64,
            size_bytes=2316,
            taxon_count=9,
            bucket="taxonomy-packs",
            object_name="packs/core/2026.07.09.1/core.json",
            active=True,
        )
    )
    fake_session.execute = AsyncMock(side_effect=[user_result, pack_result])

    response = taxa_client.get(
        "/v1/taxa/packs/core",
        headers={"Authorization": "Bearer fake"},
    )

    assert response.status_code == 200
    assert response.json()["checksum_sha256"] == "a" * 64
    assert response.json()["download_url"].startswith("https://storage.test/")
