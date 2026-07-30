"""Tests for POST /v1/observations/{id}/identify (Phase 7 slice 2)."""

from __future__ import annotations

import base64
import json
from collections.abc import AsyncIterator, Iterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import ReleaseLane, Settings
from app.core.parent_consent import (
    ADULT_QA_CONSENT_POLICY_VERSION,
    CurrentParentConsentRequiredError,
)
from app.db import models
from app.db.session import get_db_session
from app.main import create_app
from tests.helpers.auth import stub_token_verifier

_FIREBASE_UID = "firebase-kid-001"
_USER_ID = "01J0KIDID0000000000000ULID"
_GROUP_ID = "01J0GROUPID00000000000ULID"
_OBS_ID = "01J0OBSID00000000000000ULID"
_PHOTO_ID = "01J0PHOTOID00000000000ULID"
_PARENT_ID = "01J0PARENTID0000000000ULID"


def _fresh_cv_token() -> str:
    payload = base64.urlsafe_b64encode(json.dumps({"exp": 4_102_444_800}).encode())
    return f"header.{payload.decode().rstrip('=')}.signature"


class _StubStorage:
    """Stub PhotoStorage that returns fixed bytes."""

    def __init__(self, bytes_to_return: bytes = b"fake-jpeg-bytes") -> None:
        self.bytes_to_return = bytes_to_return
        self.fetch_calls: list[tuple[str, str]] = []

    def generate_put_url(self, **_: object) -> tuple[str, object]:
        raise NotImplementedError

    def fetch_object_bytes(self, *, bucket: str, object_name: str) -> bytes:
        self.fetch_calls.append((bucket, object_name))
        return self.bytes_to_return

    def copy_object(
        self,
        *,
        src_bucket: str,
        src_object: str,
        dst_bucket: str,
        dst_object: str,
    ) -> None:
        raise NotImplementedError

    def delete_object(self, *, bucket: str, object_name: str) -> None:
        raise NotImplementedError

    def generate_get_url(self, **_: object) -> tuple[str, object]:
        raise NotImplementedError


def _stub_token_verifier(monkeypatch: pytest.MonkeyPatch) -> None:
    """Back-compat shim that delegates to the shared helper."""
    stub_token_verifier(monkeypatch, uid=_FIREBASE_UID, role="kid", group_id=_GROUP_ID)


def _build_client(
    fake_session: AsyncMock,
    *,
    inat_token: str | None = None,
    storage: _StubStorage | None = None,
    inat_client: httpx.AsyncClient | None = None,
    cv_enabled: bool = True,
    release_lane: ReleaseLane = "local",
    benchmark_approved: bool | None = None,
    qualification_enabled: bool = False,
) -> Iterator[TestClient]:
    app = create_app(
        Settings(
            env="local",
            app_version="test",
            release_lane=release_lane,
            inat_cv_token=_fresh_cv_token() if inat_token is None else inat_token,
            inat_cv_enabled=cv_enabled,
            inat_cv_disclosure_approved=cv_enabled,
            inat_cv_benchmark_approved=(
                cv_enabled if benchmark_approved is None else benchmark_approved
            ),
            inat_cv_qualification_enabled=qualification_enabled,
            inat_cv_qualification_key="q" * 32 if qualification_enabled else "",
            moderation_provider="azure_content_safety",
            content_safety_endpoint="https://content-safety.example.test",
            content_safety_key="configured-key",
        )
    )
    # FastAPI resolves all dependencies before the handler runs, so even
    # tests that 401/403/404 before touching storage would otherwise pull
    # the real GcsSignedUrlGenerator (which needs ADC). Always inject a
    # stub.
    app.state.signed_url_generator = storage if storage is not None else _StubStorage()
    if inat_client is not None:
        app.state.inat_cv_client = inat_client

    async def override() -> AsyncIterator[AsyncSession]:
        yield fake_session

    app.dependency_overrides[get_db_session] = override
    with TestClient(app, raise_server_exceptions=True) as test_client:
        yield test_client


@pytest.fixture
def fake_session() -> AsyncMock:
    return AsyncMock(spec=AsyncSession)


def _user_row(*, parent_user_id: str | None = None) -> models.User:
    return models.User(
        id=_USER_ID,
        firebase_uid=_FIREBASE_UID,
        role="kid",
        display_name="Kid Name",
        parent_user_id=parent_user_id,
    )


def _obs_with_photo(
    *,
    photo_status: str = "clean",
    moderation_status: str = "clean",
    moderation_source: str = "azure",
    revocation: models.PhotoRevocation | None = None,
) -> tuple[models.Observation, models.Photo, models.PhotoRevocation | None]:
    photo = models.Photo(
        id=_PHOTO_ID,
        user_id=_USER_ID,
        bucket="hinterland-photos-test",
        object_name=f"observations/{_PHOTO_ID}.jpg",
        status=photo_status,
        attachment_status="attached",
        content_type="image/jpeg",
        sha256="a" * 64,
    )
    obs = models.Observation(
        id=_OBS_ID,
        user_id=_USER_ID,
        group_id=_GROUP_ID,
        photo_id=_PHOTO_ID,
        latitude=39.1,
        longitude=-84.5,
        moderation_status=moderation_status,
        moderation_source=moderation_source,
    )
    return obs, photo, revocation


def _wire_session(
    fake_session: AsyncMock,
    *,
    user: models.User | None,
    obs_photo: tuple[
        models.Observation,
        models.Photo,
        models.PhotoRevocation | None,
    ]
    | None = None,
    cache: models.CvSuggestionCache | None = None,
    catalog_rows: list[models.SpeciesCache] | None = None,
) -> None:
    user_result = MagicMock()
    user_result.scalar_one_or_none = MagicMock(return_value=user)

    obs_result = MagicMock()
    obs_result.one_or_none = MagicMock(return_value=obs_photo)
    cache_result = MagicMock()
    cache_result.scalar_one_or_none = MagicMock(return_value=cache)
    catalog_result = MagicMock()
    catalog_result.scalars.return_value.all.return_value = (
        catalog_rows
        if catalog_rows is not None
        else [
            models.SpeciesCache(
                taxon_id=12345,
                scientific_name="Cardinalis cardinalis",
                common_name="Northern Cardinal",
                iconic_taxon="Aves",
                active=True,
                source_payload={},
            ),
            models.SpeciesCache(
                taxon_id=67890,
                scientific_name="Cardinalis sinuatus",
                common_name="Pyrrhuloxia",
                iconic_taxon="Aves",
                active=True,
                source_payload={},
            ),
        ]
    )

    side_effects: list[Any] = [user_result]
    if user is not None:
        side_effects.append(obs_result)
        if obs_photo is not None:
            side_effects.append(cache_result)
            side_effects.append(catalog_result)

    fake_session.execute = AsyncMock(side_effect=side_effects)


# ---------------------------------------------------------------------------


def test_identify_requires_bearer_token(fake_session: AsyncMock) -> None:
    for client in _build_client(fake_session):
        response = client.post(f"/v1/observations/{_OBS_ID}/identify")
        assert response.status_code == 401


def test_identify_403_when_no_postgres_user(
    monkeypatch: pytest.MonkeyPatch, fake_session: AsyncMock
) -> None:
    _stub_token_verifier(monkeypatch)
    _wire_session(fake_session, user=None)
    for client in _build_client(fake_session):
        response = client.post(
            f"/v1/observations/{_OBS_ID}/identify",
            headers={"Authorization": "Bearer fake"},
        )
        assert response.status_code == 403


def test_identify_404_when_observation_missing_or_wrong_owner(
    monkeypatch: pytest.MonkeyPatch, fake_session: AsyncMock
) -> None:
    _stub_token_verifier(monkeypatch)
    _wire_session(fake_session, user=_user_row(), obs_photo=None)
    for client in _build_client(fake_session):
        response = client.post(
            f"/v1/observations/{_OBS_ID}/identify",
            headers={"Authorization": "Bearer fake"},
        )
        assert response.status_code == 404


def test_identify_returns_cv_unavailable_when_no_token(
    monkeypatch: pytest.MonkeyPatch, fake_session: AsyncMock
) -> None:
    _stub_token_verifier(monkeypatch)
    _wire_session(fake_session, user=_user_row(), obs_photo=_obs_with_photo())
    storage = _StubStorage()
    for client in _build_client(fake_session, inat_token="", storage=storage):
        response = client.post(
            f"/v1/observations/{_OBS_ID}/identify",
            headers={"Authorization": "Bearer fake"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["cv_unavailable"] is True
        assert body["no_matches"] is False
        assert body["suggestions"] == []
        # Storage was never read -- short-circuited before any iNat work.
        assert storage.fetch_calls == []


def test_identify_is_disabled_by_default_even_with_token(
    monkeypatch: pytest.MonkeyPatch, fake_session: AsyncMock
) -> None:
    _stub_token_verifier(monkeypatch)
    _wire_session(fake_session, user=_user_row(), obs_photo=_obs_with_photo())
    storage = _StubStorage()
    for client in _build_client(fake_session, storage=storage, cv_enabled=False):
        response = client.post(
            f"/v1/observations/{_OBS_ID}/identify",
            headers={"Authorization": "Bearer fake"},
        )
        assert response.status_code == 200
        assert response.json()["cv_unavailable"] is True
        assert storage.fetch_calls == []


def test_adult_qa_identify_requires_exact_parent_disclosure_before_photo_read(
    monkeypatch: pytest.MonkeyPatch,
    fake_session: AsyncMock,
) -> None:
    _stub_token_verifier(monkeypatch)
    storage = _StubStorage()
    _wire_session(
        fake_session,
        user=_user_row(parent_user_id=_PARENT_ID),
        obs_photo=_obs_with_photo(),
    )
    require_consent = AsyncMock(side_effect=CurrentParentConsentRequiredError("required"))
    monkeypatch.setattr(
        "app.api.routes.observations.require_linked_parent_consent_for_policy",
        require_consent,
    )

    for client in _build_client(
        fake_session,
        storage=storage,
        release_lane="adult_qa",
    ):
        response = client.post(
            f"/v1/observations/{_OBS_ID}/identify",
            headers={"Authorization": "Bearer fake"},
        )

    assert response.status_code == 409
    assert "provider disclosure" in response.json()["error"]["message"]
    assert storage.fetch_calls == []
    require_consent.assert_awaited_once_with(
        fake_session,
        parent_user_id=_PARENT_ID,
        policy_version=ADULT_QA_CONSENT_POLICY_VERSION,
    )


@respx.mock
def test_adult_qa_benchmark_key_allows_qualification_without_enabling_helper(
    monkeypatch: pytest.MonkeyPatch,
    fake_session: AsyncMock,
) -> None:
    _stub_token_verifier(monkeypatch)
    _wire_session(
        fake_session,
        user=_user_row(parent_user_id=_PARENT_ID),
        obs_photo=_obs_with_photo(),
    )
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
                    }
                ]
            },
        )
    )
    storage = _StubStorage()
    with patch(
        "app.api.routes.observations.require_linked_parent_consent_for_policy",
        new=AsyncMock(),
    ):
        for client in _build_client(
            fake_session,
            storage=storage,
            release_lane="adult_qa",
            benchmark_approved=False,
            qualification_enabled=True,
        ):
            disabled = client.post(
                f"/v1/observations/{_OBS_ID}/identify",
                headers={"Authorization": "Bearer fake"},
            )
            assert disabled.status_code == 200
            assert disabled.json()["cv_unavailable"] is True
            assert storage.fetch_calls == []

            _wire_session(
                fake_session,
                user=_user_row(parent_user_id=_PARENT_ID),
                obs_photo=_obs_with_photo(),
            )
            qualified = client.post(
                f"/v1/observations/{_OBS_ID}/identify",
                headers={
                    "Authorization": "Bearer fake",
                    "X-Hinterland-QA-CV-Qualification": "q" * 32,
                },
            )

    assert qualified.status_code == 200
    assert qualified.json()["suggestions"][0]["taxon_id"] == 12345
    assert storage.fetch_calls == [("hinterland-photos-test", f"observations/{_PHOTO_ID}.jpg")]


def test_identify_rejects_photo_that_is_not_clean(
    monkeypatch: pytest.MonkeyPatch, fake_session: AsyncMock
) -> None:
    _stub_token_verifier(monkeypatch)
    _wire_session(
        fake_session,
        user=_user_row(),
        obs_photo=_obs_with_photo(photo_status="pending", moderation_status="pending"),
    )
    storage = _StubStorage()
    for client in _build_client(fake_session, storage=storage):
        response = client.post(
            f"/v1/observations/{_OBS_ID}/identify",
            headers={"Authorization": "Bearer fake"},
        )
        assert response.status_code == 409
        assert storage.fetch_calls == []


@pytest.mark.parametrize(
    "obs_photo",
    [
        _obs_with_photo(moderation_source="none"),
        _obs_with_photo(moderation_source="noop"),
        _obs_with_photo(revocation=MagicMock(spec=models.PhotoRevocation)),
    ],
)
def test_identify_requires_real_or_adult_approval_without_active_revocation(
    monkeypatch: pytest.MonkeyPatch,
    fake_session: AsyncMock,
    obs_photo: tuple[
        models.Observation,
        models.Photo,
        models.PhotoRevocation | None,
    ],
) -> None:
    _stub_token_verifier(monkeypatch)
    _wire_session(fake_session, user=_user_row(), obs_photo=obs_photo)
    storage = _StubStorage()
    for client in _build_client(fake_session, storage=storage):
        response = client.post(
            f"/v1/observations/{_OBS_ID}/identify",
            headers={"Authorization": "Bearer fake"},
        )
        assert response.status_code == 409
        assert storage.fetch_calls == []


def test_identify_reuses_canonical_hash_and_model_cache(
    monkeypatch: pytest.MonkeyPatch, fake_session: AsyncMock
) -> None:
    _stub_token_verifier(monkeypatch)
    cached = models.CvSuggestionCache(
        photo_sha256="a" * 64,
        model_version="inat-cv-v1",
        suggestions=[
            {
                "taxon_id": 12345,
                "common_name": "Northern Cardinal",
                "scientific_name": "Cardinalis cardinalis",
                "score": 91.0,
            }
        ],
    )
    _wire_session(
        fake_session,
        user=_user_row(),
        obs_photo=_obs_with_photo(),
        cache=cached,
    )
    storage = _StubStorage()
    for client in _build_client(fake_session, storage=storage):
        response = client.post(
            f"/v1/observations/{_OBS_ID}/identify",
            headers={"Authorization": "Bearer fake"},
        )
        assert response.status_code == 200
        assert response.json()["suggestions"][0]["taxon_id"] == 12345
        assert storage.fetch_calls == []


@respx.mock
def test_identify_happy_path_returns_top_3(
    monkeypatch: pytest.MonkeyPatch, fake_session: AsyncMock
) -> None:
    _stub_token_verifier(monkeypatch)
    _wire_session(fake_session, user=_user_row(), obs_photo=_obs_with_photo())
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
                ]
            },
        )
    )
    storage = _StubStorage()
    for client in _build_client(fake_session, storage=storage):
        response = client.post(
            f"/v1/observations/{_OBS_ID}/identify",
            headers={"Authorization": "Bearer fake"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["cv_unavailable"] is False
        assert body["no_matches"] is False
        assert len(body["suggestions"]) == 2
        assert body["suggestions"][0]["taxon_id"] == 12345
        assert body["suggestions"][0]["common_name"] == "Northern Cardinal"
        assert body["suggestions"][0]["score"] == 92.5
        # Storage WAS read with the observations photo key.
        assert storage.fetch_calls == [("hinterland-photos-test", f"observations/{_PHOTO_ID}.jpg")]


@respx.mock
def test_identify_returns_cv_unavailable_on_inat_5xx(
    monkeypatch: pytest.MonkeyPatch, fake_session: AsyncMock
) -> None:
    _stub_token_verifier(monkeypatch)
    _wire_session(fake_session, user=_user_row(), obs_photo=_obs_with_photo())
    respx.post("https://api.inaturalist.org/v1/computervision/score_image").mock(
        return_value=httpx.Response(503)
    )
    for client in _build_client(fake_session, storage=_StubStorage()):
        response = client.post(
            f"/v1/observations/{_OBS_ID}/identify",
            headers={"Authorization": "Bearer fake"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["cv_unavailable"] is True
        assert body["no_matches"] is False
        assert body["suggestions"] == []


@respx.mock
@pytest.mark.parametrize(
    "provider_response",
    [
        httpx.Response(429),
        httpx.Response(200, text="not-json"),
        httpx.Response(200, json=[]),
        httpx.Response(200, json={"unexpected": []}),
    ],
)
def test_identify_returns_cv_unavailable_on_throttle_or_malformed_success(
    monkeypatch: pytest.MonkeyPatch,
    fake_session: AsyncMock,
    provider_response: httpx.Response,
) -> None:
    _stub_token_verifier(monkeypatch)
    _wire_session(fake_session, user=_user_row(), obs_photo=_obs_with_photo())
    respx.post("https://api.inaturalist.org/v1/computervision/score_image").mock(
        return_value=provider_response
    )
    for client in _build_client(fake_session, storage=_StubStorage()):
        response = client.post(
            f"/v1/observations/{_OBS_ID}/identify",
            headers={"Authorization": "Bearer fake"},
        )
        assert response.status_code == 200
        assert response.json()["cv_unavailable"] is True
        assert response.json()["suggestions"] == []


@respx.mock
def test_identify_returns_empty_on_inat_4xx(
    monkeypatch: pytest.MonkeyPatch, fake_session: AsyncMock
) -> None:
    """4xx other than auth -> iNat couldn't classify; not an outage."""
    _stub_token_verifier(monkeypatch)
    _wire_session(fake_session, user=_user_row(), obs_photo=_obs_with_photo())
    respx.post("https://api.inaturalist.org/v1/computervision/score_image").mock(
        return_value=httpx.Response(400, json={"error": "bad image"})
    )
    for client in _build_client(fake_session, storage=_StubStorage()):
        response = client.post(
            f"/v1/observations/{_OBS_ID}/identify",
            headers={"Authorization": "Bearer fake"},
        )
        assert response.status_code == 200
        body = response.json()
        # iNat said "no idea" -- the call succeeded, just empty.
        assert body["cv_unavailable"] is False
        assert body["no_matches"] is True
        assert body["suggestions"] == []
