from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes import observations as observations_routes
from app.core.config import Settings
from app.db import models
from app.db.session import get_db_session
from app.dispatcher.types import Reward
from app.main import create_app
from tests.helpers.auth import stub_token_verifier

_FIREBASE_UID = "firebase-kid-001"
_USER_ID = "01J0KIDID0000000000000ULID"
_GROUP_ID = "01J0GROUPID00000000000ULID"
_PHOTO_ID = "01J0PHOTOID00000000000ULID"


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
        bucket="dragonfly-photos-test",
        object_name=f"pending/{_PHOTO_ID}.jpg",
        status=status,
        content_type="image/jpeg",
    )


def _wire_session(
    fake_session: AsyncMock,
    *,
    user: models.User | None,
    photo: models.Photo | None = None,
    membership_id: str | None = None,
    group: models.Group | None = None,
    existing_observation: models.Observation | None = None,
) -> None:
    """Wire `session.execute(...)` for the route's statement sequence.

    Pending photo: user -> photo -> membership-update -> group (the group
    select is the first statement inside the dispatch block, so it is
    wired only when the membership check passes). Non-pending photo:
    user -> photo -> existing-observation lookup (the idempotent-replay
    probe). Exhausting the side_effect list raises inside the route's
    dispatch try/except -- which is exactly the bug that hid the rewards
    contract from this suite, so keep the wiring in step with the route's
    execute sequence.
    """
    user_result = MagicMock()
    user_result.scalar_one_or_none = MagicMock(return_value=user)

    photo_result = MagicMock()
    photo_result.scalar_one_or_none = MagicMock(return_value=photo)

    membership_result = MagicMock()
    membership_result.scalar_one_or_none = MagicMock(return_value=membership_id)

    group_result = MagicMock()
    group_result.scalar_one_or_none = MagicMock(return_value=group)

    existing_result = MagicMock()
    existing_result.scalar_one_or_none = MagicMock(return_value=existing_observation)

    side_effects: list[Any] = [user_result]
    if user is not None:
        side_effects.append(photo_result)
        if photo is not None:
            if photo.status == "pending":
                side_effects.append(membership_result)
                if membership_id is not None:
                    side_effects.append(group_result)
            else:
                side_effects.append(existing_result)

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
    assert "clean" in response.json()["error"]["message"]


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


def _first_find_reward() -> Reward:
    return Reward(
        type="first_find",
        title="First find!",
        detail="You found a new species",
        icon="sparkles",
        weight=80,
        payload={"taxon_id": 12345},
    )


def test_create_happy_path(
    monkeypatch: pytest.MonkeyPatch,
    observations_client: TestClient,
    fake_session: AsyncMock,
) -> None:
    """Create succeeds AND the dispatcher contract holds: rewards land in
    the 201 body and `dispatched_at` is stamped (second commit).

    The dispatcher itself is faked -- handler behavior has its own suite;
    this pins the route-level celebration contract, which a session-stub
    exhaustion bug used to silently skip (the route's except-Exception
    swallowed the StopIteration and the suite passed on the failure path).
    """
    _stub_token_verifier(monkeypatch)
    _wire_session(
        fake_session,
        user=_user_row(),
        photo=_photo_row(),
        membership_id="01J0MEMBERID0000000000ULID",
    )

    async def fake_dispatch(ctx: object, handlers: object) -> list[Reward]:
        return [_first_find_reward()]

    monkeypatch.setattr(observations_routes, "dispatch", fake_dispatch)

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
    assert body["latitude"] == 39.1031
    assert body["longitude"] == -84.5120
    assert body["taxon_id"] == 12345
    assert body["species_name"] == "Northern Cardinal"
    # geohash4 length 4, base32-ish
    assert body["geohash4"] is not None
    assert len(body["geohash4"]) == 4

    # The celebration payload: dispatcher rewards serialized into the 201.
    assert len(body["rewards"]) == 1
    assert body["rewards"][0]["type"] == "first_find"
    assert body["rewards"][0]["title"] == "First find!"
    assert body["rewards"][0]["weight"] == 80
    assert body["rewards"][0]["payload"] == {"taxon_id": 12345}

    fake_session.add.assert_called_once()
    obs: models.Observation = fake_session.add.call_args.args[0]
    assert isinstance(obs, models.Observation)
    assert obs.user_id == _USER_ID
    assert obs.group_id == _GROUP_ID
    assert obs.photo_id == _PHOTO_ID
    assert obs.geohash4 == body["geohash4"]
    # dispatched_at stamped on dispatcher success, persisted by the second
    # commit (first = observation row, second = dispatched_at).
    assert obs.dispatched_at is not None
    # Created WITH a taxon: the write-once first-assignment marker is set
    # at insert time, so a later clear-and-repick can never re-dispatch.
    assert obs.taxon_first_assigned_at is not None
    assert fake_session.commit.await_count == 2


def test_create_without_taxon_leaves_first_assignment_marker_null(
    monkeypatch: pytest.MonkeyPatch,
    observations_client: TestClient,
    fake_session: AsyncMock,
) -> None:
    """The live mobile flow creates taxonless; the marker stays NULL so
    the first species pick via PATCH dispatches."""
    _stub_token_verifier(monkeypatch)
    _wire_session(
        fake_session,
        user=_user_row(),
        photo=_photo_row(),
        membership_id="01J0MEMBERID0000000000ULID",
    )

    async def fake_dispatch(ctx: object, handlers: object) -> list[Reward]:
        return []

    monkeypatch.setattr(observations_routes, "dispatch", fake_dispatch)

    payload = {
        "photo_id": _PHOTO_ID,
        "latitude": 39.1031,
        "longitude": -84.5120,
    }
    response = observations_client.post(
        "/v1/observations",
        json=payload,
        headers={"Authorization": "Bearer fake"},
    )
    assert response.status_code == 201

    obs: models.Observation = fake_session.add.call_args.args[0]
    assert obs.taxon_id is None
    assert obs.taxon_first_assigned_at is None


def test_create_201_with_empty_rewards_when_dispatch_fails(
    monkeypatch: pytest.MonkeyPatch,
    observations_client: TestClient,
    fake_session: AsyncMock,
) -> None:
    """Dispatcher failure never surfaces: the kid still gets their 201,
    rewards are empty, and `dispatched_at` stays NULL for the nightly
    replay to pick up."""
    _stub_token_verifier(monkeypatch)
    _wire_session(
        fake_session,
        user=_user_row(),
        photo=_photo_row(),
        membership_id="01J0MEMBERID0000000000ULID",
    )

    async def exploding_dispatch(ctx: object, handlers: object) -> list[Reward]:
        raise RuntimeError("handler exploded")

    monkeypatch.setattr(observations_routes, "dispatch", exploding_dispatch)

    response = observations_client.post(
        "/v1/observations",
        json=_valid_payload(),
        headers={"Authorization": "Bearer fake"},
    )
    assert response.status_code == 201
    assert response.json()["rewards"] == []

    obs: models.Observation = fake_session.add.call_args.args[0]
    assert obs.dispatched_at is None
    # Only the observation-insert commit ran; the dispatched_at commit
    # never happened.
    assert fake_session.commit.await_count == 1


def _existing_observation() -> models.Observation:
    return models.Observation(
        id="01J0EXISTINGOBS0000000ULID",
        user_id=_USER_ID,
        group_id=_GROUP_ID,
        photo_id=_PHOTO_ID,
        latitude=39.1031,
        longitude=-84.5120,
        geohash4="dnp1",
        taxon_id=None,
        species_name=None,
        place_name=None,
    )


def _wire_duplicate_photo_conflict(
    fake_session: AsyncMock,
    *,
    existing_observation: models.Observation | None,
) -> None:
    """Wire the lost-201-retry shape: insert hits uq_observations_photo_id,
    then the route probes for the existing observation after rollback.

    Execute sequence: user -> photo -> membership-update -> (commit raises)
    -> existing-observation lookup.
    """
    user_result = MagicMock()
    user_result.scalar_one_or_none = MagicMock(return_value=_user_row())
    photo_result = MagicMock()
    photo_result.scalar_one_or_none = MagicMock(return_value=_photo_row())
    membership_result = MagicMock()
    membership_result.scalar_one_or_none = MagicMock(return_value="01J0MEMBERID0000000000ULID")
    existing_result = MagicMock()
    existing_result.scalar_one_or_none = MagicMock(return_value=existing_observation)

    fake_session.execute = AsyncMock(
        side_effect=[user_result, photo_result, membership_result, existing_result]
    )
    fake_session.add = MagicMock()
    fake_session.commit = AsyncMock(
        side_effect=IntegrityError(
            "INSERT INTO observations ...",
            {},
            Exception('duplicate key value violates unique constraint "uq_observations_photo_id"'),
        )
    )
    fake_session.refresh = AsyncMock()


def test_create_replays_existing_observation_on_duplicate_photo(
    monkeypatch: pytest.MonkeyPatch,
    observations_client: TestClient,
    fake_session: AsyncMock,
) -> None:
    """uq_observations_photo_id violation with an existing row = a retry
    after a lost create response. The route rolls back (undoing the second
    membership bump) and replays the existing observation instead of
    stranding the client on a 409 it can never resolve."""
    _stub_token_verifier(monkeypatch)
    existing = _existing_observation()
    _wire_duplicate_photo_conflict(fake_session, existing_observation=existing)

    response = observations_client.post(
        "/v1/observations",
        json=_valid_payload(),
        headers={"Authorization": "Bearer fake"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["id"] == existing.id
    assert body["rewards"] == []
    fake_session.rollback.assert_awaited_once()


def test_create_409_when_duplicate_but_no_existing_row(
    monkeypatch: pytest.MonkeyPatch,
    observations_client: TestClient,
    fake_session: AsyncMock,
) -> None:
    """IntegrityError without a findable existing observation (FK race,
    concurrent delete) still 409s rather than 500ing."""
    _stub_token_verifier(monkeypatch)
    _wire_duplicate_photo_conflict(fake_session, existing_observation=None)

    response = observations_client.post(
        "/v1/observations",
        json=_valid_payload(),
        headers={"Authorization": "Bearer fake"},
    )
    assert response.status_code == 409
    assert "already attached" in response.json()["error"]["message"]
    fake_session.rollback.assert_awaited_once()


def test_create_replays_existing_observation_when_photo_already_moderated(
    monkeypatch: pytest.MonkeyPatch,
    observations_client: TestClient,
    fake_session: AsyncMock,
) -> None:
    """Lost 201 + moderation already ran: the photo is no longer `pending`,
    but the retry must still recover the kid's observation."""
    _stub_token_verifier(monkeypatch)
    existing = _existing_observation()
    _wire_session(
        fake_session,
        user=_user_row(),
        photo=_photo_row(status="clean"),
        existing_observation=existing,
    )

    response = observations_client.post(
        "/v1/observations",
        json=_valid_payload(),
        headers={"Authorization": "Bearer fake"},
    )
    assert response.status_code == 201
    assert response.json()["id"] == existing.id


# ---------------------------------------------------------------------------
# GET /v1/observations/me
# ---------------------------------------------------------------------------


def _obs_with_photo(
    obs_id: str,
    *,
    taxon_id: int | None = None,
    species_name: str | None = None,
) -> tuple[models.Observation, models.Photo]:
    photo = models.Photo(
        id=f"PHOTO{obs_id[:21]}",
        user_id=_USER_ID,
        bucket="dragonfly-photos-test",
        object_name=f"pending/{obs_id}.jpg",
        status="pending",
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
    )
    obs.created_at = datetime(2026, 5, 9, 23, 0, 0, tzinfo=UTC)
    return obs, photo


def _wire_list_query(
    fake_session: AsyncMock,
    *,
    user: models.User | None,
    rows: list[tuple[models.Observation, models.Photo]] | None = None,
) -> None:
    user_result = MagicMock()
    user_result.scalar_one_or_none = MagicMock(return_value=user)

    list_result = MagicMock()
    list_result.all = MagicMock(return_value=rows or [])

    side_effects: list[Any] = [user_result]
    if user is not None:
        side_effects.append(list_result)

    fake_session.execute = AsyncMock(side_effect=side_effects)


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
    # Photo metadata included
    assert body["items"][0]["photo_object_name"].startswith("pending/")
    assert body["items"][0]["photo_status"] == "pending"


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
