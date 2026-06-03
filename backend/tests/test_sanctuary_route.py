"""Tests for ``GET /v1/sanctuary/me``.

Mirrors the TestClient + dependency_override pattern from
``test_expedition_endpoints.py``: a real FastAPI app, a stubbed token
verifier, and an ``AsyncMock(spec=AsyncSession)`` whose ``execute()``
yields canned rows.

The Sanctuary content cache is shared process state; the autouse
``_reset_content_cache`` fixture clears it between tests so a content
edit during one test does not bleed into the next.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.db import models
from app.db.session import get_db_session
from app.main import create_app
from app.sanctuary.content import reset_sanctuary_content_cache
from tests.helpers.auth import stub_token_verifier

_FIREBASE_UID = "firebase-kid-001"
_USER_ID = "01J0KIDID0000000000000ULID"
_GROUP_ID = "01J0GROUPID00000000000ULID"
_OTHER_USER_ID = "01J0OTHERUSERID00000000ULI"

# Real content ids from PR #96.
_MEADOW_COARSE_PLANTAE = "meadow_coarse_plantae"


@pytest.fixture(autouse=True)
def _reset_content_cache() -> Iterator[None]:
    reset_sanctuary_content_cache()
    yield
    reset_sanctuary_content_cache()


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


def _user() -> models.User:
    return models.User(id=_USER_ID, firebase_uid=_FIREBASE_UID, role="kid", display_name="Kid")


def _scalar(value: object | None) -> MagicMock:
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=value)
    return result


def _scalars_list(rows: list[object]) -> MagicMock:
    result = MagicMock()
    scalars = MagicMock()
    scalars.all = MagicMock(return_value=rows)
    result.scalars = MagicMock(return_value=scalars)
    return result


def _wire_session(
    fake_session: AsyncMock,
    *,
    user: models.User | None,
    zone_states: list[object] | None = None,
    elements: list[object] | None = None,
    events: list[object] | None = None,
) -> None:
    """Sequence the four SELECTs the route issues, in order:

    1. SELECT users WHERE firebase_uid = ?
    2. SELECT sanctuary_zone_state WHERE user_id = ?
    3. SELECT sanctuary_elements   WHERE user_id = ? ORDER BY unlocked_at
    4. SELECT sanctuary_events     WHERE user_id = ? ORDER BY created_at DESC LIMIT 10
    """
    fake_session.execute = AsyncMock(
        side_effect=[
            _scalar(user),
            _scalars_list(zone_states or []),
            _scalars_list(elements or []),
            _scalars_list(events or []),
        ]
    )


def _zone_state(
    zone_id: str,
    *,
    observation_count: int,
    depth_tier: int = 0,
) -> MagicMock:
    row = MagicMock()
    row.user_id = _USER_ID
    row.zone_id = zone_id
    row.observation_count = observation_count
    row.depth_tier = depth_tier
    return row


def _element(
    *,
    element_id: str,
    zone_id: str = "meadow",
    element_type: str = "coarse",
    taxon_id: int | None = None,
    payload: dict[str, Any] | None = None,
    source_observation_id: str | None = "01J0OBS00000000000000ULID0",
    unlocked_at: datetime | None = None,
) -> MagicMock:
    row = MagicMock()
    row.user_id = _USER_ID
    row.zone_id = zone_id
    row.element_id = element_id
    row.element_type = element_type
    row.taxon_id = taxon_id
    row.payload = payload if payload is not None else {}
    row.source_observation_id = source_observation_id
    row.unlocked_at = unlocked_at or datetime(2026, 6, 3, 12, 0, 0, tzinfo=UTC)
    return row


def _event(
    *,
    event_type: str = "world_unlock",
    zone_id: str | None = "meadow",
    element_id: str | None = _MEADOW_COARSE_PLANTAE,
    title: str = "Your meadow woke up",
    detail: str | None = "A real observation brought new life here.",
    payload: dict[str, Any] | None = None,
    created_at: datetime | None = None,
) -> MagicMock:
    row = MagicMock()
    row.user_id = _USER_ID
    row.event_type = event_type
    row.zone_id = zone_id
    row.element_id = element_id
    row.title = title
    row.detail = detail
    row.payload = payload if payload is not None else {}
    row.created_at = created_at or datetime(2026, 6, 3, 12, 0, 0, tzinfo=UTC)
    return row


# ---------------------------------------------------------------------------
# Group A: auth / privacy
# ---------------------------------------------------------------------------


def test_unauthenticated_request_returns_401(fake_session: AsyncMock) -> None:
    for client in _build_client(fake_session):
        response = client.get("/v1/sanctuary/me")
        assert response.status_code == 401


def test_authenticated_user_fetches_own_sanctuary(
    monkeypatch: pytest.MonkeyPatch, fake_session: AsyncMock
) -> None:
    _stub_token_verifier(monkeypatch)
    _wire_session(fake_session, user=_user())
    for client in _build_client(fake_session):
        response = client.get("/v1/sanctuary/me", headers={"Authorization": "Bearer fake"})
    assert response.status_code == 200
    body = response.json()
    assert len(body["zones"]) == 7


def test_user_id_query_param_is_silently_ignored(
    monkeypatch: pytest.MonkeyPatch, fake_session: AsyncMock
) -> None:
    """The route does NOT accept ``user_id`` -- a hostile caller passing
    it gets the same response as if the param were never set."""
    _stub_token_verifier(monkeypatch)
    _wire_session(fake_session, user=_user())
    for client in _build_client(fake_session):
        response = client.get(
            f"/v1/sanctuary/me?user_id={_OTHER_USER_ID}",
            headers={"Authorization": "Bearer fake"},
        )
    assert response.status_code == 200
    # The session was queried for the AUTHENTICATED user's firebase_uid,
    # never the spoofed user_id (the WHERE clause is bound at query
    # construction, not from the query string).
    first_call = fake_session.execute.await_args_list[0]
    rendered = str(first_call.args[0]).lower()
    assert "firebase_uid" in rendered


def test_response_contains_no_location_fields(
    monkeypatch: pytest.MonkeyPatch, fake_session: AsyncMock
) -> None:
    _stub_token_verifier(monkeypatch)
    _wire_session(
        fake_session,
        user=_user(),
        zone_states=[_zone_state("meadow", observation_count=5, depth_tier=5)],
        elements=[
            _element(
                element_id=_MEADOW_COARSE_PLANTAE,
                # Even if a malicious snapshot tries to slip in latitude,
                # the DTO does not expose it. We DO surface ``payload``
                # but only via the existing planner shapes -- a smuggled
                # location key would show up here, so the test scans
                # ALL keys recursively.
                payload={"iconic_taxon": "Plantae"},
            )
        ],
        events=[_event()],
    )
    for client in _build_client(fake_session):
        response = client.get("/v1/sanctuary/me", headers={"Authorization": "Bearer fake"})
    assert response.status_code == 200
    forbidden = {"latitude", "longitude", "geohash", "geohash4", "place_name"}
    _assert_no_forbidden_keys(response.json(), forbidden)


def _assert_no_forbidden_keys(value: object, forbidden: set[str]) -> None:
    if isinstance(value, dict):
        for key, sub in value.items():
            assert key not in forbidden, f"forbidden location key {key!r} found in response"
            _assert_no_forbidden_keys(sub, forbidden)
    elif isinstance(value, list):
        for sub in value:
            _assert_no_forbidden_keys(sub, forbidden)


def test_403_when_no_postgres_user(
    monkeypatch: pytest.MonkeyPatch, fake_session: AsyncMock
) -> None:
    _stub_token_verifier(monkeypatch)
    _wire_session(fake_session, user=None)
    for client in _build_client(fake_session):
        response = client.get("/v1/sanctuary/me", headers={"Authorization": "Bearer fake"})
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# Group B: empty state
# ---------------------------------------------------------------------------


def test_user_with_no_state_gets_seven_locked_zones(
    monkeypatch: pytest.MonkeyPatch, fake_session: AsyncMock
) -> None:
    _stub_token_verifier(monkeypatch)
    _wire_session(fake_session, user=_user())
    for client in _build_client(fake_session):
        response = client.get("/v1/sanctuary/me", headers={"Authorization": "Bearer fake"})
    assert response.status_code == 200
    body = response.json()
    assert len(body["zones"]) == 7
    for zone in body["zones"]:
        assert zone["unlocked"] is False
        assert zone["observation_count"] == 0
        assert zone["depth_tier"] == 0
        assert zone["next_threshold"] == 1


def test_empty_state_lists_empty(monkeypatch: pytest.MonkeyPatch, fake_session: AsyncMock) -> None:
    _stub_token_verifier(monkeypatch)
    _wire_session(fake_session, user=_user())
    for client in _build_client(fake_session):
        response = client.get("/v1/sanctuary/me", headers={"Authorization": "Bearer fake"})
    body = response.json()
    assert body["elements"] == []
    assert body["recent_events"] == []
    assert body["journal"] == []


def test_empty_state_has_starter_guide_message(
    monkeypatch: pytest.MonkeyPatch, fake_session: AsyncMock
) -> None:
    _stub_token_verifier(monkeypatch)
    _wire_session(fake_session, user=_user())
    for client in _build_client(fake_session):
        response = client.get("/v1/sanctuary/me", headers={"Authorization": "Bearer fake"})
    body = response.json()
    assert body["guide_message"]["speaker"] == "dragonfly"
    assert body["guide_message"]["text"]


def test_empty_state_returns_one_to_three_mystery_cues(
    monkeypatch: pytest.MonkeyPatch, fake_session: AsyncMock
) -> None:
    _stub_token_verifier(monkeypatch)
    _wire_session(fake_session, user=_user())
    for client in _build_client(fake_session):
        response = client.get("/v1/sanctuary/me", headers={"Authorization": "Bearer fake"})
    body = response.json()
    assert 1 <= len(body["mystery_cues"]) <= 3


# ---------------------------------------------------------------------------
# Group C: populated state
# ---------------------------------------------------------------------------


def test_zone_unlocked_when_observation_count_positive(
    monkeypatch: pytest.MonkeyPatch, fake_session: AsyncMock
) -> None:
    _stub_token_verifier(monkeypatch)
    _wire_session(
        fake_session,
        user=_user(),
        zone_states=[_zone_state("meadow", observation_count=5, depth_tier=5)],
    )
    for client in _build_client(fake_session):
        response = client.get("/v1/sanctuary/me", headers={"Authorization": "Bearer fake"})
    body = response.json()
    meadow = next(z for z in body["zones"] if z["zone_id"] == "meadow")
    assert meadow["unlocked"] is True
    assert meadow["observation_count"] == 5
    assert meadow["depth_tier"] == 5
    # All other zones are still locked.
    locked = [z for z in body["zones"] if z["zone_id"] != "meadow"]
    assert all(not z["unlocked"] for z in locked)


@pytest.mark.parametrize(
    ("count", "expected_next"),
    [(0, 1), (1, 3), (2, 3), (3, 5), (5, 10), (9, 10), (10, 20), (20, 50), (49, 50), (50, None)],
)
def test_next_threshold_table(
    monkeypatch: pytest.MonkeyPatch,
    fake_session: AsyncMock,
    count: int,
    expected_next: int | None,
) -> None:
    _stub_token_verifier(monkeypatch)
    _wire_session(
        fake_session,
        user=_user(),
        zone_states=[_zone_state("meadow", observation_count=count, depth_tier=0)],
    )
    for client in _build_client(fake_session):
        response = client.get("/v1/sanctuary/me", headers={"Authorization": "Bearer fake"})
    body = response.json()
    meadow = next(z for z in body["zones"] if z["zone_id"] == "meadow")
    assert meadow["next_threshold"] == expected_next


def test_elements_merge_authored_content(
    monkeypatch: pytest.MonkeyPatch, fake_session: AsyncMock
) -> None:
    """A stored ``meadow_coarse_plantae`` element returns the authored
    title / detail / icon from ``content/sanctuary/coarse_unlocks.json``
    (PR #96), not the snapshot payload."""
    _stub_token_verifier(monkeypatch)
    _wire_session(
        fake_session,
        user=_user(),
        elements=[
            _element(
                element_id=_MEADOW_COARSE_PLANTAE,
                payload={"iconic_taxon": "Plantae"},
            )
        ],
    )
    for client in _build_client(fake_session):
        response = client.get("/v1/sanctuary/me", headers={"Authorization": "Bearer fake"})
    body = response.json()
    assert len(body["elements"]) == 1
    element = body["elements"][0]
    # Authored copy from content/sanctuary/coarse_unlocks.json.
    assert element["title"] == "Plants in the meadow"
    assert element["icon"] == "sanctuary.meadow.plantae"
    assert "meadow" in element["detail"].lower()


def test_recent_events_newest_first_and_journal_oldest_first(
    monkeypatch: pytest.MonkeyPatch, fake_session: AsyncMock
) -> None:
    _stub_token_verifier(monkeypatch)
    # Three events stored newest-first per the DESC query order.
    base = datetime(2026, 6, 3, 12, 0, 0, tzinfo=UTC)
    events_desc: list[object] = [
        _event(title="Third", created_at=base + timedelta(minutes=10)),
        _event(title="Second", created_at=base + timedelta(minutes=5)),
        _event(title="First", created_at=base),
    ]
    _wire_session(fake_session, user=_user(), events=events_desc)
    for client in _build_client(fake_session):
        response = client.get("/v1/sanctuary/me", headers={"Authorization": "Bearer fake"})
    body = response.json()
    assert [e["title"] for e in body["recent_events"]] == ["Third", "Second", "First"]
    # Journal is the same set, reversed (oldest first).
    assert [j["title"] for j in body["journal"]] == ["First", "Second", "Third"]


# ---------------------------------------------------------------------------
# Group D: content fallback
# ---------------------------------------------------------------------------


def test_unknown_element_id_uses_safe_fallback(
    monkeypatch: pytest.MonkeyPatch, fake_session: AsyncMock
) -> None:
    _stub_token_verifier(monkeypatch)
    _wire_session(
        fake_session,
        user=_user(),
        elements=[
            _element(
                element_id="ghost_element_no_author",
                zone_id="meadow",
                element_type="coarse",
                payload={},  # no snapshot title/detail/icon either
            )
        ],
    )
    for client in _build_client(fake_session):
        response = client.get("/v1/sanctuary/me", headers={"Authorization": "Bearer fake"})
    body = response.json()
    element = body["elements"][0]
    assert element["title"] == "Sanctuary detail"
    assert element["detail"] == "This appeared after one of your observations."
    assert element["icon"] == "sanctuary.element"


def test_missing_payload_does_not_crash(
    monkeypatch: pytest.MonkeyPatch, fake_session: AsyncMock
) -> None:
    _stub_token_verifier(monkeypatch)
    _wire_session(
        fake_session,
        user=_user(),
        elements=[_element(element_id=_MEADOW_COARSE_PLANTAE, payload={})],
    )
    for client in _build_client(fake_session):
        response = client.get("/v1/sanctuary/me", headers={"Authorization": "Bearer fake"})
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# Group E: guide + mystery cue logic
# ---------------------------------------------------------------------------


def test_zero_state_uses_general_starter_guide(
    monkeypatch: pytest.MonkeyPatch, fake_session: AsyncMock
) -> None:
    _stub_token_verifier(monkeypatch)
    _wire_session(fake_session, user=_user())
    for client in _build_client(fake_session):
        response = client.get("/v1/sanctuary/me", headers={"Authorization": "Bearer fake"})
    body = response.json()
    # Content has a "general_guide_no_rush" line authored (PR #96); that
    # is the first general line and the planner returns it for an empty
    # state.
    text = body["guide_message"]["text"]
    assert text  # non-empty
    # Should NOT be a zone-scoped meadow line, since the user has no
    # unlocked zones and no recent events.
    assert "movement in the grass" not in text


def test_guide_prefers_recent_event_zone(
    monkeypatch: pytest.MonkeyPatch, fake_session: AsyncMock
) -> None:
    _stub_token_verifier(monkeypatch)
    _wire_session(
        fake_session,
        user=_user(),
        zone_states=[_zone_state("meadow", observation_count=1, depth_tier=1)],
        events=[_event(zone_id="meadow", title="Wake-up")],
    )
    for client in _build_client(fake_session):
        response = client.get("/v1/sanctuary/me", headers={"Authorization": "Bearer fake"})
    body = response.json()
    # The first authored meadow guide is "meadow_guide_watch_grass": "Look
    # for movement in the grass..." -- assert the returned text is the
    # meadow one, not the general or a different zone.
    assert "grass" in body["guide_message"]["text"].lower()


def test_mystery_cues_prefer_locked_zones_then_quiet(
    monkeypatch: pytest.MonkeyPatch, fake_session: AsyncMock
) -> None:
    _stub_token_verifier(monkeypatch)
    # meadow is deep (count >= 3), pond is quiet (count == 1), the rest
    # are locked. Locked zones come first in zone order; meadow (deep)
    # ranks last and only appears if fewer than 3 cues filled.
    _wire_session(
        fake_session,
        user=_user(),
        zone_states=[
            _zone_state("meadow", observation_count=5, depth_tier=5),
            _zone_state("pond", observation_count=1, depth_tier=1),
        ],
    )
    for client in _build_client(fake_session):
        response = client.get("/v1/sanctuary/me", headers={"Authorization": "Bearer fake"})
    body = response.json()
    cues = body["mystery_cues"]
    assert 1 <= len(cues) <= 3
    cue_zones = [c["zone_id"] for c in cues]
    # All cues must come from authored zones (PR #96 authors all 7).
    # meadow is deep so it should NOT appear unless we couldn't reach
    # 3 cues without it (we can: there are 5 locked zones).
    assert "meadow" not in cue_zones


def test_mystery_cues_returned_in_authored_zone_order(
    monkeypatch: pytest.MonkeyPatch, fake_session: AsyncMock
) -> None:
    _stub_token_verifier(monkeypatch)
    _wire_session(fake_session, user=_user())
    for client in _build_client(fake_session):
        response = client.get("/v1/sanctuary/me", headers={"Authorization": "Bearer fake"})
    cues = response.json()["mystery_cues"]
    # All 7 zones are locked; cues come from the first 3 in zone order:
    # meadow, woodland, pond.
    assert [c["zone_id"] for c in cues] == ["meadow", "woodland", "pond"]
