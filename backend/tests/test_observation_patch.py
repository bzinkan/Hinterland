"""Tests for PATCH /v1/observations/{id} (Phase 7 slice 3)."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
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
_OBS_ID = "01J0OBSID00000000000000ULID"


def _stub_token_verifier(monkeypatch: pytest.MonkeyPatch) -> None:
    """Back-compat shim that delegates to the shared helper."""
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
def patch_client(fake_session: AsyncMock) -> Iterator[TestClient]:
    yield from _build_client(fake_session)


def _user_row() -> models.User:
    return models.User(
        id=_USER_ID,
        firebase_uid=_FIREBASE_UID,
        role="kid",
        display_name="Kid Name",
    )


def _obs_row() -> models.Observation:
    obs = models.Observation(
        id=_OBS_ID,
        user_id=_USER_ID,
        group_id=_GROUP_ID,
        photo_id="01J0PHOTOID00000000000ULID",
        latitude=39.1,
        longitude=-84.5,
        taxon_id=None,
        species_name=None,
        place_name=None,
    )
    return obs


def _result(value: object) -> MagicMock:
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=value)
    return result


def _wire_session(
    fake_session: AsyncMock,
    *,
    user: models.User | None,
    obs: models.Observation | None = None,
    species_cache_hit: models.SpeciesCache | None = None,
) -> None:
    """Wire up the user lookup, observation lookup, and (optionally) the
    species cache lookup -- in that order, matching the route's call sites.
    """
    user_result = MagicMock()
    user_result.scalar_one_or_none = MagicMock(return_value=user)

    obs_result = MagicMock()
    obs_result.scalar_one_or_none = MagicMock(return_value=obs)

    species_result = MagicMock()
    species_result.scalar_one_or_none = MagicMock(return_value=species_cache_hit)

    side_effects: list[Any] = [user_result]
    if user is not None:
        side_effects.append(obs_result)
        if obs is not None and species_cache_hit is not None:
            # Species cache lookup happens only when taxon_id was set
            # without an explicit species_name. Tests that don't go down
            # this path don't pre-stage a species result.
            side_effects.append(species_result)
        elif obs is not None and species_cache_hit is None:
            # Tests can opt in to the cache-miss path by passing
            # species_cache_hit=None AND a taxon-only payload; the
            # services helper will execute another query for the row,
            # which we also stub as None here. Harmless if unused.
            side_effects.append(species_result)

    fake_session.execute = AsyncMock(side_effect=side_effects)
    fake_session.commit = AsyncMock()
    fake_session.refresh = AsyncMock()
    fake_session.add = MagicMock()
    fake_session.flush = AsyncMock()


# ---------------------------------------------------------------------------


def test_patch_requires_bearer_token(patch_client: TestClient) -> None:
    response = patch_client.patch(f"/v1/observations/{_OBS_ID}", json={"place_name": "x"})
    assert response.status_code == 401


def test_patch_422_on_empty_body(
    monkeypatch: pytest.MonkeyPatch,
    patch_client: TestClient,
    fake_session: AsyncMock,
) -> None:
    _stub_token_verifier(monkeypatch)
    response = patch_client.patch(
        f"/v1/observations/{_OBS_ID}",
        json={},
        headers={"Authorization": "Bearer fake"},
    )
    assert response.status_code == 422


def test_patch_403_when_no_postgres_user(
    monkeypatch: pytest.MonkeyPatch,
    patch_client: TestClient,
    fake_session: AsyncMock,
) -> None:
    _stub_token_verifier(monkeypatch)
    _wire_session(fake_session, user=None)
    response = patch_client.patch(
        f"/v1/observations/{_OBS_ID}",
        json={"place_name": "Cincinnati"},
        headers={"Authorization": "Bearer fake"},
    )
    assert response.status_code == 403


def test_patch_404_when_observation_missing_or_wrong_owner(
    monkeypatch: pytest.MonkeyPatch,
    patch_client: TestClient,
    fake_session: AsyncMock,
) -> None:
    _stub_token_verifier(monkeypatch)
    _wire_session(fake_session, user=_user_row(), obs=None)
    response = patch_client.patch(
        f"/v1/observations/{_OBS_ID}",
        json={"place_name": "Cincinnati"},
        headers={"Authorization": "Bearer fake"},
    )
    assert response.status_code == 404


def test_patch_only_place_name_no_species_lookup(
    monkeypatch: pytest.MonkeyPatch,
    patch_client: TestClient,
    fake_session: AsyncMock,
) -> None:
    _stub_token_verifier(monkeypatch)
    _wire_session(fake_session, user=_user_row(), obs=_obs_row())

    response = patch_client.patch(
        f"/v1/observations/{_OBS_ID}",
        json={"place_name": "Cincinnati, OH"},
        headers={"Authorization": "Bearer fake"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["place_name"] == "Cincinnati, OH"
    fake_session.commit.assert_awaited_once()


def test_patch_rejects_taxon_and_client_species_name(
    monkeypatch: pytest.MonkeyPatch,
    patch_client: TestClient,
    fake_session: AsyncMock,
) -> None:
    """Derived taxonomy cannot bypass the revisioned identification flow."""
    _stub_token_verifier(monkeypatch)
    _wire_session(fake_session, user=_user_row(), obs=_obs_row())

    response = patch_client.patch(
        f"/v1/observations/{_OBS_ID}",
        json={"taxon_id": 12345, "species_name": "Northern Cardinal"},
        headers={"Authorization": "Bearer fake"},
    )
    assert response.status_code == 422


def test_patch_rejects_taxon_even_when_it_exists_in_catalog(
    monkeypatch: pytest.MonkeyPatch,
    patch_client: TestClient,
    fake_session: AsyncMock,
) -> None:
    _stub_token_verifier(monkeypatch)
    cached = models.SpeciesCache(
        taxon_id=12345,
        scientific_name="Cardinalis cardinalis",
        common_name="Northern Cardinal",
        iconic_taxon="Aves",
        source_payload={},
    )
    _wire_session(
        fake_session,
        user=_user_row(),
        obs=_obs_row(),
        species_cache_hit=cached,
    )

    response = patch_client.patch(
        f"/v1/observations/{_OBS_ID}",
        json={"taxon_id": 12345},
        headers={"Authorization": "Bearer fake"},
    )
    assert response.status_code == 422


def test_identification_uses_catalog_name_and_queues_rebuild(
    monkeypatch: pytest.MonkeyPatch,
    patch_client: TestClient,
    fake_session: AsyncMock,
) -> None:
    _stub_token_verifier(monkeypatch)
    observation = _obs_row()
    observation.identification_revision = 1
    observation.moderation_status = "clean"
    catalog = models.SpeciesCache(
        taxon_id=12345,
        scientific_name="Cardinalis cardinalis",
        common_name="Northern Cardinal",
        iconic_taxon="Aves",
        active=True,
        source_payload={},
    )
    fake_session.execute = AsyncMock(
        side_effect=[
            _result(_user_row()),
            MagicMock(),  # per-user advisory lock
            _result(observation),
            _result(catalog),
        ]
    )
    fake_session.commit = AsyncMock()
    fake_session.refresh = AsyncMock()
    rebuild = models.DerivedStateRebuild(
        id="01J0REBUILD000000000000ULID",
        user_id=_USER_ID,
        trigger_observation_id=_OBS_ID,
        status="queued",
        attempt_count=0,
    )
    enqueue = AsyncMock(return_value=rebuild)
    monkeypatch.setattr("app.api.routes.observations.enqueue_rebuild", enqueue)

    response = patch_client.post(
        f"/v1/observations/{_OBS_ID}/identification",
        json={
            "taxon_id": 12345,
            "source": "catalog",
            "expected_revision": 1,
        },
        headers={"Authorization": "Bearer fake"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["observation"]["species_name"] == "Northern Cardinal"
    assert body["observation"]["identification_revision"] == 2
    assert body["rebuild_id"] == rebuild.id
    assert observation.identification_source == "catalog"
    enqueue.assert_awaited_once()
    fake_session.commit.assert_awaited_once()
    statements = [str(call.args[0]) for call in fake_session.execute.await_args_list]
    assert "pg_advisory_xact_lock" in statements[1]
    assert "FOR UPDATE" in statements[2]


def test_identification_revision_conflict_does_not_queue_rebuild(
    monkeypatch: pytest.MonkeyPatch,
    patch_client: TestClient,
    fake_session: AsyncMock,
) -> None:
    _stub_token_verifier(monkeypatch)
    observation = _obs_row()
    observation.identification_revision = 3
    observation.moderation_status = "clean"
    fake_session.execute = AsyncMock(
        side_effect=[_result(_user_row()), MagicMock(), _result(observation)]
    )
    enqueue = AsyncMock()
    monkeypatch.setattr("app.api.routes.observations.enqueue_rebuild", enqueue)

    response = patch_client.post(
        f"/v1/observations/{_OBS_ID}/identification",
        json={"source": "unknown", "expected_revision": 2},
        headers={"Authorization": "Bearer fake"},
    )

    assert response.status_code == 409
    enqueue.assert_not_awaited()


@pytest.mark.parametrize(
    "payload",
    [
        {"source": "catalog", "expected_revision": 1},
        {"source": "manual_text", "expected_revision": 1},
        {"source": "unknown", "taxon_id": 12, "expected_revision": 1},
        {
            "source": "manual_text",
            "taxon_id": 12,
            "manual_text": "bird",
            "expected_revision": 1,
        },
    ],
)
def test_identification_rejects_mismatched_shapes(
    monkeypatch: pytest.MonkeyPatch,
    patch_client: TestClient,
    fake_session: AsyncMock,
    payload: dict[str, object],
) -> None:
    del fake_session
    _stub_token_verifier(monkeypatch)
    response = patch_client.post(
        f"/v1/observations/{_OBS_ID}/identification",
        json=payload,
        headers={"Authorization": "Bearer fake"},
    )
    assert response.status_code == 422
