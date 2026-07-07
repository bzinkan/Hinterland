from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from typing import Any
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
_GROUP_ID = "01J0GROUPID00000000000ULID"


def _id(kind: str, n: int) -> str:
    return f"01J0{kind}{n:019d}"


def _stub_token_verifier(monkeypatch: pytest.MonkeyPatch) -> None:
    stub_token_verifier(monkeypatch, uid=_FIREBASE_UID, role="kid", group_id=_GROUP_ID)


def _build_client(fake_session: AsyncMock) -> Iterator[TestClient]:
    app = create_app(Settings(env="local", app_version="test"))

    async def override() -> AsyncIterator[AsyncSession]:
        yield fake_session

    app.dependency_overrides[get_db_session] = override
    with TestClient(app, raise_server_exceptions=True) as test_client:
        yield test_client


@pytest.fixture
def fake_session() -> AsyncMock:
    return AsyncMock(spec=AsyncSession)


@pytest.fixture
def dex_client(fake_session: AsyncMock) -> Iterator[TestClient]:
    yield from _build_client(fake_session)


def _user_row() -> models.User:
    return models.User(
        id=_USER_ID,
        firebase_uid=_FIREBASE_UID,
        role="kid",
        display_name="Kid Name",
    )


def _dex_row(
    dex_id: str,
    *,
    taxon_id: int,
    species_name: str | None,
    first_seen_at: datetime,
    photo_status: str = "clean",
    species_cache: models.SpeciesCache | None = None,
    observation_count: int = 1,
    latest_seen_at: datetime | None = None,
) -> tuple[
    models.DexEntry,
    models.Observation,
    models.Photo,
    models.SpeciesCache | None,
    int,
    datetime,
]:
    obs = models.Observation(
        id=f"01J0OBS{dex_id[-19:]}",
        user_id=_USER_ID,
        group_id=_GROUP_ID,
        photo_id=f"01J0PIC{dex_id[-19:]}",
        latitude=39.1,
        longitude=-84.5,
        geohash4="dnp1",
        taxon_id=taxon_id,
        species_name=species_name,
        place_name=None,
    )
    obs.created_at = first_seen_at
    photo = models.Photo(
        id=obs.photo_id,
        user_id=_USER_ID,
        bucket="hinterland-photos-test",
        object_name=f"observations/{obs.photo_id}.jpg",
        status=photo_status,
        content_type="image/jpeg",
    )
    dex = models.DexEntry(
        id=dex_id,
        user_id=_USER_ID,
        group_id=_GROUP_ID,
        taxon_id=taxon_id,
        species_name=species_name,
        first_observation_id=obs.id,
        first_seen_at=first_seen_at,
    )
    return (
        dex,
        obs,
        photo,
        species_cache,
        observation_count,
        latest_seen_at or first_seen_at,
    )


def _wire_dex_query(
    fake_session: AsyncMock,
    *,
    user: models.User | None,
    rows: list[tuple[Any, ...]] | None = None,
) -> None:
    user_result = MagicMock()
    user_result.scalar_one_or_none = MagicMock(return_value=user)

    list_result = MagicMock()
    list_result.all = MagicMock(return_value=rows or [])

    side_effects: list[Any] = [user_result]
    if user is not None:
        side_effects.append(list_result)

    fake_session.execute = AsyncMock(side_effect=side_effects)


def test_dex_requires_bearer_token(dex_client: TestClient) -> None:
    response = dex_client.get("/v1/dex/me")
    assert response.status_code == 401


def test_dex_403_when_no_postgres_user(
    monkeypatch: pytest.MonkeyPatch,
    dex_client: TestClient,
    fake_session: AsyncMock,
) -> None:
    _stub_token_verifier(monkeypatch)
    _wire_dex_query(fake_session, user=None)

    response = dex_client.get("/v1/dex/me", headers={"Authorization": "Bearer fake"})
    assert response.status_code == 403


def test_dex_returns_empty_for_new_user(
    monkeypatch: pytest.MonkeyPatch,
    dex_client: TestClient,
    fake_session: AsyncMock,
) -> None:
    _stub_token_verifier(monkeypatch)
    _wire_dex_query(fake_session, user=_user_row(), rows=[])

    response = dex_client.get("/v1/dex/me", headers={"Authorization": "Bearer fake"})
    assert response.status_code == 200
    assert response.json() == {"items": [], "next_cursor": None}


def test_dex_returns_verified_species_with_cache_and_counts(
    monkeypatch: pytest.MonkeyPatch,
    dex_client: TestClient,
    fake_session: AsyncMock,
) -> None:
    _stub_token_verifier(monkeypatch)
    first_seen = datetime(2026, 7, 6, 12, 0, tzinfo=UTC)
    latest_seen = datetime(2026, 7, 7, 12, 0, tzinfo=UTC)
    species = models.SpeciesCache(
        taxon_id=12345,
        common_name="Yellow Cosmos",
        scientific_name="Cosmos sulphureus",
        iconic_taxon="Plantae",
        source_payload={},
    )
    _wire_dex_query(
        fake_session,
        user=_user_row(),
        rows=[
            _dex_row(
                _id("DEX", 1),
                taxon_id=12345,
                species_name="Yellow Cosmos",
                first_seen_at=first_seen,
                species_cache=species,
                observation_count=3,
                latest_seen_at=latest_seen,
            )
        ],
    )

    response = dex_client.get("/v1/dex/me", headers={"Authorization": "Bearer fake"})

    assert response.status_code == 200
    body = response.json()
    assert body["next_cursor"] is None
    assert body["items"][0] == {
        "id": _id("DEX", 1),
        "taxon_id": 12345,
        "species_name": "Yellow Cosmos",
        "common_name": "Yellow Cosmos",
        "scientific_name": "Cosmos sulphureus",
        "iconic_taxon": "Plantae",
        "first_observation_id": _id("OBS", 1),
        "first_photo_id": _id("PIC", 1),
        "first_photo_status": "clean",
        "first_seen_at": "2026-07-06T12:00:00Z",
        "observation_count": 3,
        "latest_seen_at": "2026-07-07T12:00:00Z",
    }


def test_dex_allows_missing_species_cache(
    monkeypatch: pytest.MonkeyPatch,
    dex_client: TestClient,
    fake_session: AsyncMock,
) -> None:
    _stub_token_verifier(monkeypatch)
    first_seen = datetime(2026, 7, 6, 12, 0, tzinfo=UTC)
    _wire_dex_query(
        fake_session,
        user=_user_row(),
        rows=[
            _dex_row(
                _id("DEX", 1),
                taxon_id=12345,
                species_name="Yellow Cosmos",
                first_seen_at=first_seen,
                species_cache=None,
            )
        ],
    )

    response = dex_client.get("/v1/dex/me", headers={"Authorization": "Bearer fake"})

    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["common_name"] is None
    assert item["scientific_name"] is None
    assert item["iconic_taxon"] is None
    assert item["observation_count"] == 1


def test_dex_returns_next_cursor_when_more_pages_exist(
    monkeypatch: pytest.MonkeyPatch,
    dex_client: TestClient,
    fake_session: AsyncMock,
) -> None:
    _stub_token_verifier(monkeypatch)
    seen = datetime(2026, 7, 6, 12, 0, tzinfo=UTC)
    rows = [
        _dex_row(_id("DEX", 2), taxon_id=2, species_name="B", first_seen_at=seen),
        _dex_row(_id("DEX", 1), taxon_id=1, species_name="A", first_seen_at=seen),
    ]
    _wire_dex_query(fake_session, user=_user_row(), rows=rows)

    response = dex_client.get("/v1/dex/me?limit=1", headers={"Authorization": "Bearer fake"})

    assert response.status_code == 200
    body = response.json()
    assert len(body["items"]) == 1
    assert body["next_cursor"] == _id("DEX", 2)


def test_dex_rejects_malformed_cursor(
    monkeypatch: pytest.MonkeyPatch,
    dex_client: TestClient,
) -> None:
    _stub_token_verifier(monkeypatch)
    response = dex_client.get(
        "/v1/dex/me?before=short",
        headers={"Authorization": "Bearer fake"},
    )
    assert response.status_code == 422
