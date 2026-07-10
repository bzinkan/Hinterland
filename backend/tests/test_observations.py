import io
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import Response
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes.observations import (
    ObservationCreateRequest,
    _create_request_hash,
    _decode_observed_cursor,
    _encode_observed_cursor,
    _normalized_location,
    create_observation,
    derive_child_presentation_status,
)
from app.core.auth import CurrentUser
from app.core.config import Settings
from app.core.storage import StorageObjectProperties
from app.db import models
from app.db.session import get_db_session
from app.dispatcher.registry import HANDLERS
from app.dispatcher.types import Context, Reward
from app.main import create_app
from tests.helpers.auth import stub_token_verifier

_FIREBASE_UID = "firebase-kid-001"
_USER_ID = "01J0KIDID0000000000000ULID"
_GROUP_ID = "01J0GROUPID00000000000ULID"
_PHOTO_ID = "01J0PHOTOID00000000000ULID"
_IDEMPOTENCY_KEY = "01J0SUBMIT0000000000000ULID"


def test_ecology_tags_remain_part_of_the_idempotent_create_contract() -> None:
    flower = ObservationCreateRequest(
        photo_id=_PHOTO_ID,
        ecology_tags={"life_stage": "flower"},
    )
    leaf = ObservationCreateRequest(
        photo_id=_PHOTO_ID,
        ecology_tags={"life_stage": "leaf"},
    )
    assert flower.ecology_tags == {"life_stage": "flower"}
    assert _create_request_hash(
        flower,
        geohash4=None,
        location_source="none",
    ) != _create_request_hash(
        leaf,
        geohash4=None,
        location_source="none",
    )


def _jpeg_bytes() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (320, 240), (30, 100, 60)).save(output, format="JPEG")
    return output.getvalue()


class _StubObservationStorage:
    def __init__(self) -> None:
        self.raw = _jpeg_bytes()
        self.deleted: list[str] = []

    def get_object_properties(self, **_: object) -> StorageObjectProperties:
        return StorageObjectProperties(len(self.raw), "image/jpeg", "etag")

    def fetch_object_bytes(self, **_: object) -> bytes:
        return self.raw

    def put_object_bytes(self, **_: object) -> None:
        return None

    def delete_object(self, *, bucket: str, object_name: str) -> None:
        del bucket
        self.deleted.append(object_name)

    def generate_put_url(self, **_: object) -> tuple[str, datetime]:
        return "https://upload.test", datetime.now(UTC) + timedelta(minutes=1)

    def generate_get_url(self, **_: object) -> tuple[str, datetime]:
        return "https://download.test", datetime.now(UTC) + timedelta(minutes=1)

    def copy_object(self, **_: object) -> None:  # pragma: no cover
        raise NotImplementedError


def _stub_token_verifier(
    monkeypatch: pytest.MonkeyPatch,
    *,
    uid: str = _FIREBASE_UID,
    group_id: str | None = _GROUP_ID,
) -> None:
    """Back-compat shim that delegates to the shared helper."""
    stub_token_verifier(monkeypatch, uid=uid, role="kid", group_id=group_id)


def _build_client(fake_session: AsyncMock) -> Iterator[TestClient]:
    app = create_app(Settings(env="local", app_version="test"))
    app.state.signed_url_generator = _StubObservationStorage()

    async def override() -> AsyncIterator[AsyncSession]:
        yield fake_session

    app.dependency_overrides[get_db_session] = override
    with TestClient(app, raise_server_exceptions=True) as test_client:
        yield test_client


@pytest.fixture
def fake_session() -> AsyncMock:
    return AsyncMock(spec=AsyncSession)


@pytest.fixture
def observations_client(fake_session: AsyncMock) -> Iterator[TestClient]:
    yield from _build_client(fake_session)


def _user_row() -> models.User:
    return models.User(
        id=_USER_ID,
        firebase_uid=_FIREBASE_UID,
        role="kid",
        display_name="Kid Name",
    )


def _photo_row(status: str = "pending") -> models.Photo:
    return models.Photo(
        id=_PHOTO_ID,
        user_id=_USER_ID,
        bucket="hinterland-photos-test",
        object_name=f"pending/{_PHOTO_ID}.jpg",
        status=status,
        attachment_status="reserved",
        submission_key=_IDEMPOTENCY_KEY,
        content_type="image/jpeg",
    )


def _group_row() -> models.Group:
    return models.Group(
        id=_GROUP_ID,
        name="Family",
        join_code="ABC123",
        owner_user_id=_USER_ID,
    )


def _wire_session(
    fake_session: AsyncMock,
    *,
    user: models.User | None,
    photo: models.Photo | None = None,
    membership_id: str | None = None,
    group: models.Group | None = None,
    taxon_id: int | None = 12345,
    catalog: models.SpeciesCache | None = None,
) -> None:
    """Wire `session.execute(...)` for the user -> photo -> membership-update sequence.

    Each `.execute()` returns a Result-like with the corresponding shape.
    """
    user_result = MagicMock()
    user_result.scalar_one_or_none = MagicMock(return_value=user)

    photo_result = MagicMock()
    photo_result.scalar_one_or_none = MagicMock(return_value=photo)

    membership_result = MagicMock()
    membership_result.scalar_one_or_none = MagicMock(return_value=membership_id)
    replay_result = MagicMock()
    replay_result.scalar_one_or_none = MagicMock(return_value=None)
    catalog_result = MagicMock()
    catalog_result.scalar_one_or_none = MagicMock(
        return_value=catalog
        or (
            models.SpeciesCache(
                taxon_id=taxon_id,
                scientific_name="Cardinalis cardinalis",
                common_name="Northern Cardinal",
                iconic_taxon="Aves",
                active=True,
                source_payload={},
            )
            if taxon_id is not None
            else None
        )
    )
    lock_result = MagicMock()
    group_result = MagicMock()
    group_result.scalar_one_or_none = MagicMock(return_value=group)

    side_effects: list[Any] = [user_result]
    if user is not None:
        side_effects.append(photo_result)
        if (
            photo is not None
            and photo.status == "pending"
            and photo.attachment_status == "reserved"
        ):
            side_effects.append(replay_result)
            if taxon_id is not None:
                side_effects.append(catalog_result)
            side_effects.append(lock_result)
            side_effects.append(replay_result)
            side_effects.append(photo_result)
            side_effects.append(membership_result)
            if group is not None:
                side_effects.append(group_result)

    fake_session.execute = AsyncMock(side_effect=side_effects)
    fake_session.add = MagicMock()
    fake_session.commit = AsyncMock()
    fake_session.refresh = AsyncMock()


def _valid_payload() -> dict[str, object]:
    return {
        "photo_id": _PHOTO_ID,
        "latitude": 39.1031,
        "longitude": -84.5120,
        "taxon_id": 12345,
        "species_name": "Northern Cardinal",
        "place_name": "Cincinnati, OH",
    }


# ---------------------------------------------------------------------------


def test_create_requires_bearer_token(observations_client: TestClient) -> None:
    response = observations_client.post("/v1/observations", json=_valid_payload())
    assert response.status_code == 401


def test_create_403_when_no_postgres_user(
    monkeypatch: pytest.MonkeyPatch,
    observations_client: TestClient,
    fake_session: AsyncMock,
) -> None:
    _stub_token_verifier(monkeypatch)
    _wire_session(fake_session, user=None)

    response = observations_client.post(
        "/v1/observations",
        json=_valid_payload(),
        headers={"Authorization": "Bearer fake"},
    )
    assert response.status_code == 403


def test_create_403_when_token_missing_group_id(
    monkeypatch: pytest.MonkeyPatch,
    observations_client: TestClient,
    fake_session: AsyncMock,
) -> None:
    _stub_token_verifier(monkeypatch, group_id=None)
    _wire_session(fake_session, user=_user_row())

    response = observations_client.post(
        "/v1/observations",
        json=_valid_payload(),
        headers={"Authorization": "Bearer fake"},
    )
    assert response.status_code == 403
    assert "group_id" in response.json()["error"]["message"]


def test_create_404_when_photo_missing_or_wrong_owner(
    monkeypatch: pytest.MonkeyPatch,
    observations_client: TestClient,
    fake_session: AsyncMock,
) -> None:
    _stub_token_verifier(monkeypatch)
    _wire_session(fake_session, user=_user_row(), photo=None)

    response = observations_client.post(
        "/v1/observations",
        json=_valid_payload(),
        headers={"Authorization": "Bearer fake"},
    )
    assert response.status_code == 404


def test_create_409_when_photo_not_pending(
    monkeypatch: pytest.MonkeyPatch,
    observations_client: TestClient,
    fake_session: AsyncMock,
) -> None:
    _stub_token_verifier(monkeypatch)
    _wire_session(fake_session, user=_user_row(), photo=_photo_row(status="clean"))

    response = observations_client.post(
        "/v1/observations",
        json=_valid_payload(),
        headers={"Authorization": "Bearer fake"},
    )
    assert response.status_code == 409
    assert "available reservation" in response.json()["error"]["message"]


def test_create_403_when_membership_missing(
    monkeypatch: pytest.MonkeyPatch,
    observations_client: TestClient,
    fake_session: AsyncMock,
) -> None:
    _stub_token_verifier(monkeypatch)
    _wire_session(
        fake_session,
        user=_user_row(),
        photo=_photo_row(),
        membership_id=None,
    )

    response = observations_client.post(
        "/v1/observations",
        json=_valid_payload(),
        headers={"Authorization": "Bearer fake"},
    )
    assert response.status_code == 403
    fake_session.add.assert_not_called()


def test_create_422_on_invalid_latitude(
    monkeypatch: pytest.MonkeyPatch,
    observations_client: TestClient,
    fake_session: AsyncMock,
) -> None:
    _stub_token_verifier(monkeypatch)
    payload = _valid_payload() | {"latitude": 91.0}

    response = observations_client.post(
        "/v1/observations",
        json=payload,
        headers={"Authorization": "Bearer fake"},
    )
    assert response.status_code == 422


def test_create_happy_path(
    monkeypatch: pytest.MonkeyPatch,
    observations_client: TestClient,
    fake_session: AsyncMock,
) -> None:
    _stub_token_verifier(monkeypatch)
    photo = _photo_row()
    _wire_session(
        fake_session,
        user=_user_row(),
        photo=photo,
        membership_id="01J0MEMBERID0000000000ULID",
    )

    response = observations_client.post(
        "/v1/observations",
        json=_valid_payload(),
        headers={"Authorization": "Bearer fake"},
    )
    assert response.status_code == 201

    body = response.json()
    assert body["user_id"] == _USER_ID
    assert body["group_id"] == _GROUP_ID
    assert body["photo_id"] == _PHOTO_ID
    assert "latitude" not in body
    assert "longitude" not in body
    assert body["child_presentation_status"] == "pending"
    assert body["taxon_id"] == 12345
    assert body["species_name"] == "Northern Cardinal"
    # geohash4 length 4, base32-ish
    assert body["geohash4"] is not None
    assert len(body["geohash4"]) == 4

    assert fake_session.add.call_count == 3 + len(HANDLERS)
    obs: models.Observation = fake_session.add.call_args_list[0].args[0]
    assert isinstance(obs, models.Observation)
    assert obs.user_id == _USER_ID
    assert obs.group_id == _GROUP_ID
    assert obs.photo_id == _PHOTO_ID
    assert obs.geohash4 == body["geohash4"]
    assert obs.latitude is None
    assert obs.longitude is None
    assert obs.submission_key == _IDEMPOTENCY_KEY
    assert photo.attachment_status == "attached"
    assert photo.object_name == f"pending/finalized/{_PHOTO_ID}.jpg"
    assert photo.canonical_object_name == photo.object_name
    assert photo.byte_count is not None and photo.byte_count > 0
    assert photo.width_px == 320
    assert photo.height_px == 240
    assert photo.sha256 is not None and len(photo.sha256) == 64
    assert photo.verified_at is not None
    fake_session.flush.assert_awaited_once_with()
    fake_session.commit.assert_awaited_once()


async def test_create_integrity_recovery_uses_cached_user_id_after_rollback(
    fake_session: AsyncMock,
) -> None:
    class RollbackSensitiveUser:
        disabled_at = None
        role = "kid"
        expired = False
        id_reads = 0

        @property
        def id(self) -> str:
            self.id_reads += 1
            if self.expired:
                raise AssertionError("expired ORM user identity was accessed after rollback")
            return _USER_ID

    user = RollbackSensitiveUser()
    photo = _photo_row()

    def scalar_result(value: object) -> MagicMock:
        result = MagicMock()
        result.scalar_one_or_none = MagicMock(return_value=value)
        return result

    no_replay = scalar_result(None)
    fake_session.execute = AsyncMock(
        side_effect=[
            scalar_result(user),
            no_replay,
            scalar_result(photo),
            MagicMock(),
            no_replay,
            scalar_result(photo),
            scalar_result("01J0MEMBERID0000000000ULID"),
            no_replay,
        ]
    )
    fake_session.add = MagicMock()
    expected_error = IntegrityError("insert observation", {}, RuntimeError("conflict"))
    fake_session.flush = AsyncMock(side_effect=expected_error)

    async def expire_on_rollback() -> None:
        user.expired = True

    fake_session.rollback = AsyncMock(side_effect=expire_on_rollback)

    with pytest.raises(IntegrityError) as raised:
        await create_observation(
            payload=ObservationCreateRequest(
                photo_id=_PHOTO_ID,
                location_source="none",
                identification_source="unknown",
            ),
            current_user=CurrentUser(
                uid=_USER_ID,
                role="kid",
                group_id=_GROUP_ID,
            ),
            session=fake_session,
            settings=Settings(
                env="local",
                observation_idempotency_required=True,
            ),
            response=Response(),
            storage=_StubObservationStorage(),
            idempotency_key=_IDEMPOTENCY_KEY,
        )

    assert raised.value is expected_error
    assert user.id_reads == 1
    fake_session.rollback.assert_awaited_once()
    fake_session.commit.assert_not_awaited()


def test_create_with_selected_taxon_returns_dispatcher_rewards(
    monkeypatch: pytest.MonkeyPatch,
    observations_client: TestClient,
    fake_session: AsyncMock,
) -> None:
    _stub_token_verifier(monkeypatch)
    photo = _photo_row()
    _wire_session(
        fake_session,
        user=_user_row(),
        photo=photo,
        membership_id="01J0MEMBERID0000000000ULID",
        group=_group_row(),
    )

    async def fake_dispatch(ctx: Context, _handlers: object) -> list[Reward]:
        assert ctx.observation.taxon_id == 12345
        assert ctx.observation.species_name == "Northern Cardinal"
        return [
            Reward(
                type="first_find",
                title="First find",
                detail="First Northern Cardinal in your Dex",
                icon="dex.first_find",
                weight=80,
                payload={"taxon_id": 12345},
            )
        ]

    monkeypatch.setattr("app.api.routes.observations.dispatch", fake_dispatch)

    response = observations_client.post(
        "/v1/observations",
        json=_valid_payload(),
        headers={"Authorization": "Bearer fake"},
    )

    assert response.status_code == 201
    body = response.json()
    assert [reward["type"] for reward in body["rewards"]] == ["first_find"]
    assert body["rewards"][0]["payload"] == {"taxon_id": 12345}
    assert fake_session.commit.await_count == 1


def test_create_dispatch_infrastructure_failure_returns_only_persisted_state(
    monkeypatch: pytest.MonkeyPatch,
    observations_client: TestClient,
    fake_session: AsyncMock,
) -> None:
    _stub_token_verifier(monkeypatch)
    photo = _photo_row()
    _wire_session(
        fake_session,
        user=_user_row(),
        photo=photo,
        membership_id="01J0MEMBERID0000000000ULID",
        group=_group_row(),
    )

    async def failing_dispatch(ctx: Context, _handlers: object) -> list[Reward]:
        # Simulate dirty ORM state written after the base-save commit but
        # before the dispatcher infrastructure fails.
        ctx.observation.dispatch_status = "partial"
        ctx.observation.rewards = [
            {
                "type": "first_find",
                "title": "dirty",
                "detail": "not committed",
                "icon": "dirty",
                "weight": 80,
                "payload": {},
            }
        ]
        raise RuntimeError("ledger connection dropped")

    async def refresh_from_database(observation: models.Observation) -> None:
        observation.dispatch_status = "pending"
        observation.rewards = []

    monkeypatch.setattr("app.api.routes.observations.dispatch", failing_dispatch)
    fake_session.refresh = AsyncMock(side_effect=refresh_from_database)

    response = observations_client.post(
        "/v1/observations",
        json=_valid_payload(),
        headers={"Authorization": "Bearer fake"},
    )

    assert response.status_code == 201
    assert response.json()["dispatch_status"] == "pending"
    assert response.json()["rewards"] == []
    fake_session.rollback.assert_awaited_once()
    fake_session.refresh.assert_awaited_once()


def test_create_dispatch_recovery_connection_failure_returns_saved_snapshot(
    monkeypatch: pytest.MonkeyPatch,
    observations_client: TestClient,
    fake_session: AsyncMock,
) -> None:
    _stub_token_verifier(monkeypatch)
    photo = _photo_row()
    _wire_session(
        fake_session,
        user=_user_row(),
        photo=photo,
        membership_id="01J0MEMBERID0000000000ULID",
        group=_group_row(),
    )

    async def failing_dispatch(ctx: Context, _handlers: object) -> list[Reward]:
        ctx.observation.dispatch_status = "complete"
        ctx.observation.rewards = [
            {
                "type": "first_find",
                "title": "dirty",
                "detail": "not durable",
                "icon": "dirty",
                "weight": 80,
                "payload": {},
            }
        ]
        raise ConnectionError("database connection dropped")

    monkeypatch.setattr("app.api.routes.observations.dispatch", failing_dispatch)
    fake_session.rollback = AsyncMock(side_effect=ConnectionError("connection is closed"))
    fake_session.refresh = AsyncMock()

    response = observations_client.post(
        "/v1/observations",
        json=_valid_payload(),
        headers={"Authorization": "Bearer fake"},
    )

    assert response.status_code == 201
    assert response.json()["dispatch_status"] == "pending"
    assert response.json()["rewards"] == []
    fake_session.rollback.assert_awaited_once()
    # A failed rollback proves the connection is unusable. Recovery must not
    # issue a refresh (or any later DB call) before returning the saved DTO.
    fake_session.refresh.assert_not_awaited()
    fake_session.commit.assert_awaited_once()


def test_create_manual_species_has_no_species_rewards(
    monkeypatch: pytest.MonkeyPatch,
    observations_client: TestClient,
    fake_session: AsyncMock,
) -> None:
    _stub_token_verifier(monkeypatch)
    photo = _photo_row()
    _wire_session(
        fake_session,
        user=_user_row(),
        photo=photo,
        membership_id="01J0MEMBERID0000000000ULID",
        group=_group_row(),
        taxon_id=None,
    )

    async def fake_dispatch(ctx: Context, _handlers: object) -> list[Reward]:
        assert ctx.observation.taxon_id is None
        assert ctx.observation.species_name == "Mystery green sprout"
        return []

    monkeypatch.setattr("app.api.routes.observations.dispatch", fake_dispatch)
    payload = _valid_payload() | {
        "taxon_id": None,
        "species_name": "Mystery green sprout",
    }

    response = observations_client.post(
        "/v1/observations",
        json=payload,
        headers={"Authorization": "Bearer fake"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["taxon_id"] is None
    assert body["species_name"] == "Mystery green sprout"
    assert body["rewards"] == []


def test_create_uses_species_name_from_local_catalog(
    monkeypatch: pytest.MonkeyPatch,
    observations_client: TestClient,
    fake_session: AsyncMock,
) -> None:
    _stub_token_verifier(monkeypatch)
    photo = _photo_row()
    _wire_session(
        fake_session,
        user=_user_row(),
        photo=photo,
        membership_id="01J0MEMBERID0000000000ULID",
        taxon_id=555,
        catalog=models.SpeciesCache(
            taxon_id=555,
            scientific_name="Acer rubrum",
            common_name="Red Maple",
            iconic_taxon="Plantae",
            active=True,
            source_payload={},
        ),
    )

    response = observations_client.post(
        "/v1/observations",
        json=_valid_payload() | {"taxon_id": 555, "species_name": "Untrusted name"},
        headers={"Authorization": "Bearer fake"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["taxon_id"] == 555
    assert body["species_name"] == "Red Maple"


def test_create_without_location_persists_no_raw_coordinates(
    monkeypatch: pytest.MonkeyPatch,
    observations_client: TestClient,
    fake_session: AsyncMock,
) -> None:
    _stub_token_verifier(monkeypatch)
    _wire_session(
        fake_session,
        user=_user_row(),
        photo=_photo_row(),
        membership_id="01J0MEMBERID0000000000ULID",
        taxon_id=None,
    )
    payload = {
        "photo_id": _PHOTO_ID,
        "taxon_id": None,
        "species_name": None,
        "location_source": "none",
    }
    response = observations_client.post(
        "/v1/observations",
        json=payload,
        headers={"Authorization": "Bearer fake"},
    )
    assert response.status_code == 201
    assert response.json()["geohash4"] is None
    assert response.json()["location_source"] == "none"
    observation = fake_session.add.call_args_list[0].args[0]
    assert observation.latitude is None
    assert observation.longitude is None


def test_create_requires_taxon_to_exist_in_local_catalog(
    monkeypatch: pytest.MonkeyPatch,
    observations_client: TestClient,
    fake_session: AsyncMock,
) -> None:
    _stub_token_verifier(monkeypatch)
    user_result = MagicMock()
    user_result.scalar_one_or_none = MagicMock(return_value=_user_row())
    photo_result = MagicMock()
    photo_result.scalar_one_or_none = MagicMock(return_value=_photo_row())
    replay_result = MagicMock()
    replay_result.scalar_one_or_none = MagicMock(return_value=None)
    catalog_result = MagicMock()
    catalog_result.scalar_one_or_none = MagicMock(return_value=None)
    fake_session.execute = AsyncMock(
        side_effect=[user_result, photo_result, replay_result, catalog_result]
    )

    response = observations_client.post(
        "/v1/observations",
        json=_valid_payload() | {"taxon_id": 999999, "species_name": "Invented"},
        headers={"Authorization": "Bearer fake"},
    )
    assert response.status_code == 422


def test_create_idempotency_replay_returns_canonical_observation(
    monkeypatch: pytest.MonkeyPatch,
    observations_client: TestClient,
    fake_session: AsyncMock,
) -> None:
    _stub_token_verifier(monkeypatch)
    key = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
    payload = ObservationCreateRequest.model_validate(_valid_payload())
    coarse, source = _normalized_location(payload)
    request_hash = _create_request_hash(payload, geohash4=coarse, location_source=source)
    record = models.ObservationIdempotency(
        user_id=_USER_ID,
        idempotency_key=key,
        operation="observation_create",
        request_hash=request_hash,
        resource_id="01ARZ3NDEKTSV4RRFFQ69G5FAW",
    )
    observation = models.Observation(
        id=record.resource_id,
        user_id=_USER_ID,
        group_id=_GROUP_ID,
        photo_id=_PHOTO_ID,
        submission_key=key,
        latitude=None,
        longitude=None,
        geohash4=coarse,
        observed_at=datetime.now(UTC),
        location_source=source,
        taxon_id=12345,
        species_name="Northern Cardinal",
        identification_source="catalog",
        identification_revision=1,
        moderation_status="pending",
        dispatch_status="complete",
        rewards=[],
    )
    results = []
    for value in (_user_row(), record, observation):
        result = MagicMock()
        result.scalar_one_or_none = MagicMock(return_value=value)
        results.append(result)
    attached_photo = _photo_row()
    attached_photo.attachment_status = "attached"
    lifecycle_result = MagicMock()
    lifecycle_result.one_or_none = MagicMock(return_value=(attached_photo, None))
    results.append(lifecycle_result)
    fake_session.execute = AsyncMock(side_effect=results)

    response = observations_client.post(
        "/v1/observations",
        json=_valid_payload(),
        headers={"Authorization": "Bearer fake", "Idempotency-Key": key},
    )
    assert response.status_code == 200
    assert response.headers["Idempotency-Replayed"] == "true"
    assert response.json()["id"] == observation.id


def test_create_changed_idempotent_payload_returns_conflict_code(
    monkeypatch: pytest.MonkeyPatch,
    observations_client: TestClient,
    fake_session: AsyncMock,
) -> None:
    _stub_token_verifier(monkeypatch)
    key = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
    record = models.ObservationIdempotency(
        user_id=_USER_ID,
        idempotency_key=key,
        operation="observation_create",
        request_hash="hash-for-a-different-payload",
        resource_id="01ARZ3NDEKTSV4RRFFQ69G5FAW",
    )
    user_result = MagicMock()
    user_result.scalar_one_or_none = MagicMock(return_value=_user_row())
    record_result = MagicMock()
    record_result.scalar_one_or_none = MagicMock(return_value=record)
    fake_session.execute = AsyncMock(side_effect=[user_result, record_result])

    response = observations_client.post(
        "/v1/observations",
        json=_valid_payload(),
        headers={"Authorization": "Bearer fake", "Idempotency-Key": key},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "idempotency_conflict"


# ---------------------------------------------------------------------------
# GET /v1/observations/me
# ---------------------------------------------------------------------------


def _obs_with_photo(
    obs_id: str,
    *,
    taxon_id: int | None = None,
    species_name: str | None = None,
) -> tuple[models.Observation, models.Photo, None]:
    photo = models.Photo(
        id=f"PHOTO{obs_id[:21]}",
        user_id=_USER_ID,
        bucket="hinterland-photos-test",
        object_name=f"pending/{obs_id}.jpg",
        status="pending",
        attachment_status="attached",
        content_type="image/jpeg",
    )
    obs = models.Observation(
        id=obs_id,
        user_id=_USER_ID,
        group_id=_GROUP_ID,
        photo_id=photo.id,
        latitude=39.1,
        longitude=-84.5,
        geohash4="dnp1",
        taxon_id=taxon_id,
        species_name=species_name,
        place_name=None,
        moderation_status="pending",
    )
    obs.created_at = datetime(2026, 5, 9, 23, 0, 0, tzinfo=UTC)
    obs.observed_at = obs.created_at
    return obs, photo, None


def _wire_list_query(
    fake_session: AsyncMock,
    *,
    user: models.User | None,
    rows: list[tuple[models.Observation, models.Photo, models.PhotoRevocation | None]]
    | None = None,
) -> None:
    user_result = MagicMock()
    user_result.scalar_one_or_none = MagicMock(return_value=user)

    list_result = MagicMock()
    list_result.all = MagicMock(return_value=rows or [])

    side_effects: list[Any] = [user_result]
    if user is not None:
        side_effects.append(list_result)

    fake_session.execute = AsyncMock(side_effect=side_effects)


def _revocation(photo: models.Photo) -> models.PhotoRevocation:
    return models.PhotoRevocation(
        photo_id=photo.id,
        review_id="01ARZ3NDEKTSV4RRFFQ69G5FAR",
        source="adult_reject",
        bucket=photo.bucket,
        source_object_name=photo.object_name,
        held_object_name=f"rejected/{photo.id}.jpg",
        expected_byte_count=100,
        expected_sha256="a" * 64,
        state="pending",
        attempt_count=0,
    )


def test_list_requires_bearer_token(observations_client: TestClient) -> None:
    response = observations_client.get("/v1/observations/me")
    assert response.status_code == 401


def test_list_403_when_no_postgres_user(
    monkeypatch: pytest.MonkeyPatch,
    observations_client: TestClient,
    fake_session: AsyncMock,
) -> None:
    _stub_token_verifier(monkeypatch)
    _wire_list_query(fake_session, user=None)

    response = observations_client.get(
        "/v1/observations/me",
        headers={"Authorization": "Bearer fake"},
    )
    assert response.status_code == 403


def test_list_returns_empty_for_new_user(
    monkeypatch: pytest.MonkeyPatch,
    observations_client: TestClient,
    fake_session: AsyncMock,
) -> None:
    _stub_token_verifier(monkeypatch)
    _wire_list_query(fake_session, user=_user_row(), rows=[])

    response = observations_client.get(
        "/v1/observations/me",
        headers={"Authorization": "Bearer fake"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["items"] == []
    assert body["next_cursor"] is None


def test_list_returns_items_newest_first_no_more_pages(
    monkeypatch: pytest.MonkeyPatch,
    observations_client: TestClient,
    fake_session: AsyncMock,
) -> None:
    _stub_token_verifier(monkeypatch)
    rows = [
        _obs_with_photo("01J0OBS00000000000000003", taxon_id=3, species_name="C"),
        _obs_with_photo("01J0OBS00000000000000002", taxon_id=2, species_name="B"),
        _obs_with_photo("01J0OBS00000000000000001", taxon_id=1, species_name="A"),
    ]
    _wire_list_query(fake_session, user=_user_row(), rows=rows)

    response = observations_client.get(
        "/v1/observations/me",
        headers={"Authorization": "Bearer fake"},
    )
    assert response.status_code == 200
    body = response.json()
    assert len(body["items"]) == 3
    assert [item["id"] for item in body["items"]] == [
        "01J0OBS00000000000000003",
        "01J0OBS00000000000000002",
        "01J0OBS00000000000000001",
    ]
    assert body["next_cursor"] is None
    assert body["items"][0]["child_presentation_status"] == "pending"
    assert body["items"][0]["submission_ulid"] is None
    forbidden = {
        "user_id",
        "group_id",
        "photo_object_name",
        "photo_status",
        "latitude",
        "longitude",
        "moderation_status",
    }
    assert forbidden.isdisjoint(body["items"][0])


def test_list_returns_next_cursor_when_more_pages_exist(
    monkeypatch: pytest.MonkeyPatch,
    observations_client: TestClient,
    fake_session: AsyncMock,
) -> None:
    """limit=2 with 3 rows in the result set => returns 2 items + next_cursor."""
    _stub_token_verifier(monkeypatch)
    # Endpoint asks for limit+1 to detect overflow; if 3 come back for limit=2,
    # has_more=True and we trim to 2.
    rows = [
        _obs_with_photo("01J0OBS00000000000000003"),
        _obs_with_photo("01J0OBS00000000000000002"),
        _obs_with_photo("01J0OBS00000000000000001"),
    ]
    _wire_list_query(fake_session, user=_user_row(), rows=rows)

    response = observations_client.get(
        "/v1/observations/me?limit=2",
        headers={"Authorization": "Bearer fake"},
    )
    assert response.status_code == 200
    body = response.json()
    assert len(body["items"]) == 2
    assert body["items"][-1]["id"] == "01J0OBS00000000000000002"
    # next_cursor is the last returned id (caller passes it as `before` next time)
    assert body["next_cursor"] == "01J0OBS00000000000000002"


def test_list_active_revocation_never_presents_clean(
    monkeypatch: pytest.MonkeyPatch,
    observations_client: TestClient,
    fake_session: AsyncMock,
) -> None:
    _stub_token_verifier(monkeypatch)
    observation, photo, _ = _obs_with_photo("01ARZ3NDEKTSV4RRFFQ69G5FAV")
    observation.moderation_status = "clean"
    photo.status = "clean"
    _wire_list_query(
        fake_session,
        user=_user_row(),
        rows=[(observation, photo, _revocation(photo))],
    )

    response = observations_client.get(
        "/v1/observations/me?order=observed",
        headers={"Authorization": "Bearer fake"},
    )

    assert response.status_code == 200
    assert response.json()["items"][0]["child_presentation_status"] == "failed"


def test_list_rejects_limit_above_max(
    monkeypatch: pytest.MonkeyPatch,
    observations_client: TestClient,
) -> None:
    _stub_token_verifier(monkeypatch)
    response = observations_client.get(
        "/v1/observations/me?limit=999",
        headers={"Authorization": "Bearer fake"},
    )
    assert response.status_code == 422


def test_list_rejects_malformed_cursor(
    monkeypatch: pytest.MonkeyPatch,
    observations_client: TestClient,
) -> None:
    _stub_token_verifier(monkeypatch)
    # ULIDs are 26 chars; anything else is rejected by the Query constraint.
    response = observations_client.get(
        "/v1/observations/me?before=tooshort",
        headers={"Authorization": "Bearer fake"},
    )
    assert response.status_code == 422


def test_observed_cursor_round_trips_versioned_timestamp_and_id() -> None:
    observed_at = datetime(2026, 7, 10, 12, 34, 56, tzinfo=UTC)
    observation_id = "01ARZ3NDEKTSV4RRFFQ69G5FAV"

    cursor = _encode_observed_cursor(observed_at, observation_id)

    assert "=" not in cursor
    assert _decode_observed_cursor(cursor) == (observed_at, observation_id)


def test_list_observed_order_returns_opaque_cursor(
    monkeypatch: pytest.MonkeyPatch,
    observations_client: TestClient,
    fake_session: AsyncMock,
) -> None:
    _stub_token_verifier(monkeypatch)
    rows = [
        _obs_with_photo("01ARZ3NDEKTSV4RRFFQ69G5FAV"),
        _obs_with_photo("01ARZ3NDEKTSV4RRFFQ69G5FAW"),
    ]
    rows[0][0].observed_at = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)
    rows[1][0].observed_at = datetime(2026, 7, 9, 12, 0, tzinfo=UTC)
    _wire_list_query(fake_session, user=_user_row(), rows=rows)

    response = observations_client.get(
        "/v1/observations/me?order=observed&limit=1",
        headers={"Authorization": "Bearer fake"},
    )

    assert response.status_code == 200
    body = response.json()
    assert [item["id"] for item in body["items"]] == [rows[0][0].id]
    assert body["next_cursor"] != rows[0][0].id
    assert _decode_observed_cursor(body["next_cursor"]) == (
        rows[0][0].observed_at,
        rows[0][0].id,
    )


@pytest.mark.parametrize(
    "query",
    [
        "order=observed&cursor=not-a-json-cursor",
        "order=observed&before=01ARZ3NDEKTSV4RRFFQ69G5FAV",
        "cursor=eyJ2IjoxfQ",
    ],
)
def test_list_rejects_invalid_or_mixed_observed_cursors(
    monkeypatch: pytest.MonkeyPatch,
    observations_client: TestClient,
    fake_session: AsyncMock,
    query: str,
) -> None:
    _stub_token_verifier(monkeypatch)
    _wire_list_query(fake_session, user=_user_row(), rows=[])

    response = observations_client.get(
        f"/v1/observations/me?{query}",
        headers={"Authorization": "Bearer fake"},
    )

    assert response.status_code == 422


@pytest.mark.parametrize(
    ("observation_status", "photo_status", "expected"),
    [
        ("clean", "clean", "clean"),
        ("pending", "pending", "pending"),
        ("processing", "pending", "processing"),
        ("pilot_private", "pending", "pilot_private"),
        ("quarantine", "quarantine", "adult_review"),
        ("failed", "pending", "failed"),
        ("clean", "pending", "failed"),
        ("unknown", "clean", "failed"),
    ],
)
def test_child_presentation_status_is_exact_and_fail_closed(
    observation_status: str,
    photo_status: str,
    expected: str,
) -> None:
    assert (
        derive_child_presentation_status(
            observation_status,
            photo_status,
            photo_attachment_status="attached",
        )
        == expected
    )


def test_child_presentation_status_denies_reserved_or_revoking_photo() -> None:
    assert (
        derive_child_presentation_status(
            "clean",
            "clean",
            photo_attachment_status="reserved",
        )
        == "failed"
    )
    assert (
        derive_child_presentation_status(
            "clean",
            "clean",
            photo_attachment_status="attached",
            revocation_active=True,
        )
        == "failed"
    )


def test_get_observation_returns_persisted_reconciliation_state(
    monkeypatch: pytest.MonkeyPatch,
    observations_client: TestClient,
    fake_session: AsyncMock,
) -> None:
    _stub_token_verifier(monkeypatch)
    observation, photo, _revocation = _obs_with_photo(
        "01J0OBS00000000000000003", taxon_id=3, species_name="Cardinal"
    )
    observation.dispatch_status = "partial"
    observation.moderation_status = "pending"
    observation.rewards = []
    user_result = MagicMock()
    user_result.scalar_one_or_none = MagicMock(return_value=_user_row())
    observation_result = MagicMock()
    observation_result.one_or_none = MagicMock(return_value=(observation, photo, None))
    fake_session.execute = AsyncMock(side_effect=[user_result, observation_result])

    response = observations_client.get(
        f"/v1/observations/{observation.id}",
        headers={"Authorization": "Bearer fake"},
    )
    assert response.status_code == 200
    assert response.json()["id"] == observation.id
    assert response.json()["dispatch_status"] == "partial"
    assert response.json()["child_presentation_status"] == "pending"
    assert "latitude" not in response.json()
    assert "longitude" not in response.json()


def test_get_observation_active_revocation_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    observations_client: TestClient,
    fake_session: AsyncMock,
) -> None:
    _stub_token_verifier(monkeypatch)
    observation, photo, _ = _obs_with_photo("01ARZ3NDEKTSV4RRFFQ69G5FAV")
    observation.moderation_status = "clean"
    photo.status = "clean"
    user_result = MagicMock()
    user_result.scalar_one_or_none = MagicMock(return_value=_user_row())
    observation_result = MagicMock()
    observation_result.one_or_none = MagicMock(
        return_value=(observation, photo, _revocation(photo))
    )
    fake_session.execute = AsyncMock(side_effect=[user_result, observation_result])

    response = observations_client.get(
        f"/v1/observations/{observation.id}",
        headers={"Authorization": "Bearer fake"},
    )

    assert response.status_code == 200
    assert response.json()["child_presentation_status"] == "failed"
