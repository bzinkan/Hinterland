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
from pathlib import Path
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


def _rows_list(rows: list[object]) -> MagicMock:
    """Column-tuple SELECT result (``.all()`` directly, no ``.scalars()``)."""
    result = MagicMock()
    result.all = MagicMock(return_value=rows)
    return result


def _wire_session(
    fake_session: AsyncMock,
    *,
    user: models.User | None,
    zone_states: list[object] | None = None,
    elements: list[object] | None = None,
    events: list[object] | None = None,
    observations: list[object] | None = None,
    progress: list[object] | None = None,
) -> None:
    """Sequence the SELECTs the route issues, in order:

    1. SELECT users WHERE firebase_uid = ?
    2. SELECT sanctuary_zone_state WHERE user_id = ?
    3. SELECT sanctuary_elements   WHERE user_id = ? ORDER BY unlocked_at
    4. SELECT sanctuary_events     WHERE user_id = ? ORDER BY created_at DESC LIMIT 10
    5. SELECT id, photo_id, moderation_status FROM observations
       WHERE id IN (...) AND user_id = ?  -- ONLY issued when at least one
       element row carries a source_observation_id (photo clean-gate)
    6. SELECT expedition_id, completed_at FROM expedition_progress
       WHERE user_id = ? AND completed_at IS NOT NULL ORDER BY completed_at

    ``observations`` rows are ``(id, photo_id, moderation_status)`` tuples;
    ``progress`` rows are ``(expedition_id, completed_at)`` tuples.
    """
    results: list[MagicMock] = [
        _scalar(user),
        _scalars_list(zone_states or []),
        _scalars_list(elements or []),
        _scalars_list(events or []),
    ]
    if any(getattr(row, "source_observation_id", None) for row in elements or []):
        results.append(_rows_list(observations or []))
    results.append(_rows_list(progress or []))
    fake_session.execute = AsyncMock(side_effect=results)


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


# ---------------------------------------------------------------------------
# Group F: delight layer (identity reflection, relationship moments,
# tiny surprises).
# ---------------------------------------------------------------------------


_FORBIDDEN_LEADERBOARD_PHRASES = (
    "best",
    "better than",
    "rank",
    "leaderboard",
    "score",
    "win",
    "compete",
    "streak",
    "in a row",
)


def test_identity_reflection_present_for_empty_state(
    monkeypatch: pytest.MonkeyPatch, fake_session: AsyncMock
) -> None:
    """Empty-state kid still gets a descriptive reflection (the
    `first_steps_outside` content entry matches when max_zones_unlocked=2
    and zero zones are unlocked)."""
    _stub_token_verifier(monkeypatch)
    _wire_session(fake_session, user=_user())
    for client in _build_client(fake_session):
        response = client.get("/v1/sanctuary/me", headers={"Authorization": "Bearer fake"})
    body = response.json()
    reflection = body["identity_reflection"]
    assert reflection is not None
    assert reflection["text"]
    assert reflection["id"]


def test_identity_reflection_selects_dominant_zone(
    monkeypatch: pytest.MonkeyPatch, fake_session: AsyncMock
) -> None:
    """meadow has strict-max observation_count; the `meadow_watcher`
    entry (dominant_zone=meadow + min_total_observations=3) matches
    before any general fallback."""
    _stub_token_verifier(monkeypatch)
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
    reflection = body["identity_reflection"]
    assert reflection is not None
    assert reflection["id"] == "meadow_watcher"
    assert "meadow" in reflection["text"].lower()


def test_identity_reflection_copy_has_no_leaderboard_terms(
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
    text = (body.get("identity_reflection") or {}).get("text", "").lower()
    for phrase in _FORBIDDEN_LEADERBOARD_PHRASES:
        assert phrase not in text, (
            f"identity_reflection.text must not contain leaderboard / streak "
            f"phrase {phrase!r}; got {text!r}"
        )


def test_relationship_moments_filtered_from_elements(
    monkeypatch: pytest.MonkeyPatch, fake_session: AsyncMock
) -> None:
    _stub_token_verifier(monkeypatch)
    _wire_session(
        fake_session,
        user=_user(),
        elements=[
            _element(
                element_id="meadow_pollination_moment",
                zone_id="meadow",
                element_type="relationship",
                payload={},
            )
        ],
    )
    for client in _build_client(fake_session):
        response = client.get("/v1/sanctuary/me", headers={"Authorization": "Bearer fake"})
    body = response.json()
    moments = body["relationship_moments"]
    assert len(moments) == 1
    assert moments[0]["element_id"] == "meadow_pollination_moment"
    assert moments[0]["zone_id"] == "meadow"
    # Authored title from PR #96 content/sanctuary/relationship_moments.json.
    assert moments[0]["title"]
    assert moments[0]["detail"]


def test_tiny_surprises_filtered_from_elements(
    monkeypatch: pytest.MonkeyPatch, fake_session: AsyncMock
) -> None:
    _stub_token_verifier(monkeypatch)
    _wire_session(
        fake_session,
        user=_user(),
        elements=[
            _element(
                element_id="meadow_surprise_drifting_petal",
                zone_id="meadow",
                element_type="surprise",
                payload={},
            )
        ],
    )
    for client in _build_client(fake_session):
        response = client.get("/v1/sanctuary/me", headers={"Authorization": "Bearer fake"})
    body = response.json()
    surprises = body["tiny_surprises"]
    assert len(surprises) == 1
    assert surprises[0]["element_id"] == "meadow_surprise_drifting_petal"
    assert surprises[0]["zone_id"] == "meadow"
    # Title is composed from the zone title at the route layer.
    assert "meadow" in surprises[0]["title"].lower()
    # Detail comes from the authored TinySurprise.description.
    assert surprises[0]["detail"]
    # Threshold is pulled from the authored content (3 for the drifting
    # petal per PR #96).
    assert surprises[0]["threshold"] == 3


def test_non_delight_element_types_excluded_from_views(
    monkeypatch: pytest.MonkeyPatch, fake_session: AsyncMock
) -> None:
    """A coarse element row must NOT appear in relationship_moments or
    tiny_surprises -- the filters look at element_type only."""
    _stub_token_verifier(monkeypatch)
    _wire_session(
        fake_session,
        user=_user(),
        elements=[
            _element(
                element_id="meadow_coarse_plantae",
                zone_id="meadow",
                element_type="coarse",
                payload={},
            )
        ],
    )
    for client in _build_client(fake_session):
        response = client.get("/v1/sanctuary/me", headers={"Authorization": "Bearer fake"})
    body = response.json()
    assert body["relationship_moments"] == []
    assert body["tiny_surprises"] == []
    # The coarse element is still in the full elements list.
    assert any(e["element_id"] == "meadow_coarse_plantae" for e in body["elements"])


# ---------------------------------------------------------------------------
# Group G: seasonal variants and sound placeholders.
# ---------------------------------------------------------------------------


_VALID_SEASONS = {"spring", "summer", "autumn", "winter"}
_VALID_BACKGROUND_TONES = {"fresh", "warm", "fading", "still"}
_EXPECTED_ZONE_ACCENT_KEYS = {
    "meadow",
    "woodland",
    "pond",
    "sky",
    "soil",
    "urban",
    "elsewhere",
}
_VALID_SOUND_KINDS = {
    "bird_chirp",
    "pond_ripple",
    "meadow_buzz",
    "wind",
    "frog_croak",
}


def test_season_block_present_on_empty_state(
    monkeypatch: pytest.MonkeyPatch, fake_session: AsyncMock
) -> None:
    """An empty-state response still carries a season block -- it is the
    visual tone, not gated on having unlocked anything."""
    _stub_token_verifier(monkeypatch)
    _wire_session(fake_session, user=_user())
    for client in _build_client(fake_session):
        response = client.get("/v1/sanctuary/me", headers={"Authorization": "Bearer fake"})
    body = response.json()
    season_info = body["season"]
    assert season_info["season"] in _VALID_SEASONS
    assert season_info["background_tone"] in _VALID_BACKGROUND_TONES
    assert set(season_info["zone_accents"].keys()) == _EXPECTED_ZONE_ACCENT_KEYS
    for accent in season_info["zone_accents"].values():
        assert isinstance(accent, str) and accent  # non-empty
    # variant_copy is whatever the authored seasonal variant for the
    # current season carries (may be present even pre-unlock as a general
    # fallback), or null if no variant authored for this season.
    assert season_info["variant_copy"] is None or isinstance(season_info["variant_copy"], str)


def test_season_variant_copy_prefers_unlocked_element(
    monkeypatch: pytest.MonkeyPatch, fake_session: AsyncMock
) -> None:
    """When a kid has unlocked ``meadow_coarse_plantae`` and the current
    season has an authored variant referencing that element, the route
    returns the variant for THAT element (not just the first match in
    content order)."""
    _stub_token_verifier(monkeypatch)
    # Force the route's "current season" to autumn by patching the date
    # source. The route calls datetime.now(tz=timezone.utc).date() which we
    # cannot patch on a builtin -- instead, the season is computed from a
    # date the helper accepts, so we patch the route's `current_season`
    # binding to a fixed-return shim.
    import app.api.routes.sanctuary as route_module

    monkeypatch.setattr(route_module, "current_season", lambda _today: "autumn")

    _wire_session(
        fake_session,
        user=_user(),
        elements=[
            _element(
                element_id="meadow_coarse_plantae",
                zone_id="meadow",
                element_type="coarse",
            )
        ],
    )
    for client in _build_client(fake_session):
        response = client.get("/v1/sanctuary/me", headers={"Authorization": "Bearer fake"})
    body = response.json()
    season_info = body["season"]
    assert season_info["season"] == "autumn"
    # PR #96 ships meadow_plantae_autumn referencing meadow_coarse_plantae;
    # that variant's authored description must come back here.
    assert season_info["variant_copy"] is not None
    assert "meadow" in season_info["variant_copy"].lower()


def test_season_does_not_expose_precise_location(
    monkeypatch: pytest.MonkeyPatch, fake_session: AsyncMock
) -> None:
    """The season block must not leak lat/lng/place_name even via
    zone_accents or variant_copy. Recursive scan of just the season
    subtree."""
    _stub_token_verifier(monkeypatch)
    _wire_session(fake_session, user=_user())
    for client in _build_client(fake_session):
        response = client.get("/v1/sanctuary/me", headers={"Authorization": "Bearer fake"})
    forbidden = {"latitude", "longitude", "geohash", "geohash4", "place_name", "lat", "lng"}
    _assert_no_forbidden_keys(response.json()["season"], forbidden)


def test_soundscapes_present_with_no_assets_available(
    monkeypatch: pytest.MonkeyPatch, fake_session: AsyncMock
) -> None:
    _stub_token_verifier(monkeypatch)
    _wire_session(fake_session, user=_user())
    for client in _build_client(fake_session):
        response = client.get("/v1/sanctuary/me", headers={"Authorization": "Bearer fake"})
    body = response.json()
    # PR ships 5 authored soundscapes (one per kind from the brief).
    assert len(body["soundscapes"]) == 5
    kinds = {s["kind"] for s in body["soundscapes"]}
    assert kinds == _VALID_SOUND_KINDS
    # No assets shipped yet -- gate stays off.
    assert body["sound_assets_available"] is False


def test_soundscapes_carry_no_asset_url_or_play_token(
    monkeypatch: pytest.MonkeyPatch, fake_session: AsyncMock
) -> None:
    """The sound placeholder DTO must not expose any field that looks
    like a playable asset -- this would let a future client autoplay
    audio against the brief."""
    _stub_token_verifier(monkeypatch)
    _wire_session(fake_session, user=_user())
    for client in _build_client(fake_session):
        response = client.get("/v1/sanctuary/me", headers={"Authorization": "Bearer fake"})
    body = response.json()
    expected_keys = {"id", "kind", "zone_id", "label", "description"}
    for entry in body["soundscapes"]:
        assert set(entry.keys()) == expected_keys, (
            f"soundscape entry has unexpected keys: {set(entry.keys())}"
        )
        # Sanity check: no url-shaped value in any string field.
        for key in ("label", "description"):
            value = entry[key]
            assert "http://" not in value and "https://" not in value


def test_soundscape_zone_id_can_be_null_for_ambient_entries(
    monkeypatch: pytest.MonkeyPatch, fake_session: AsyncMock
) -> None:
    """The authored ``general_wind`` soundscape has zone=null -- that is
    valid and should round-trip as zone_id=null on the wire."""
    _stub_token_verifier(monkeypatch)
    _wire_session(fake_session, user=_user())
    for client in _build_client(fake_session):
        response = client.get("/v1/sanctuary/me", headers={"Authorization": "Bearer fake"})
    body = response.json()
    wind = next(s for s in body["soundscapes"] if s["kind"] == "wind")
    assert wind["zone_id"] is None


# ---------------------------------------------------------------------------
# Group H: photo enrichment (clean-moderation gate, ADR 0012).
# ---------------------------------------------------------------------------

# Matches the `_element` helper's default source_observation_id.
_SOURCE_OBS_ID = "01J0OBS00000000000000ULID0"
_PHOTO_ID = "01J0PHOTO0000000000000ULID"


def test_element_photo_id_present_for_clean_observation(
    monkeypatch: pytest.MonkeyPatch, fake_session: AsyncMock
) -> None:
    _stub_token_verifier(monkeypatch)
    _wire_session(
        fake_session,
        user=_user(),
        elements=[_element(element_id=_MEADOW_COARSE_PLANTAE)],
        observations=[(_SOURCE_OBS_ID, _PHOTO_ID, "clean")],
    )
    for client in _build_client(fake_session):
        response = client.get("/v1/sanctuary/me", headers={"Authorization": "Bearer fake"})
    body = response.json()
    assert body["elements"][0]["photo_id"] == _PHOTO_ID


@pytest.mark.parametrize("moderation_status", ["pending", "quarantine", "rejected"])
def test_element_photo_id_none_when_not_clean(
    monkeypatch: pytest.MonkeyPatch,
    fake_session: AsyncMock,
    moderation_status: str,
) -> None:
    """The kid-facing Sanctuary NEVER carries a photo_id for an
    observation that has not passed moderation -- the clean gate is
    server-side (the photo-URL endpoint's adult review rules must not
    leak through this surface)."""
    _stub_token_verifier(monkeypatch)
    _wire_session(
        fake_session,
        user=_user(),
        elements=[_element(element_id=_MEADOW_COARSE_PLANTAE)],
        observations=[(_SOURCE_OBS_ID, _PHOTO_ID, moderation_status)],
    )
    for client in _build_client(fake_session):
        response = client.get("/v1/sanctuary/me", headers={"Authorization": "Bearer fake"})
    body = response.json()
    assert body["elements"][0]["photo_id"] is None


def test_element_photo_id_none_without_source_observation(
    monkeypatch: pytest.MonkeyPatch, fake_session: AsyncMock
) -> None:
    """No source_observation_id -> photo_id None AND the route skips the
    observation lookup entirely (five queries, not six)."""
    _stub_token_verifier(monkeypatch)
    _wire_session(
        fake_session,
        user=_user(),
        elements=[_element(element_id=_MEADOW_COARSE_PLANTAE, source_observation_id=None)],
    )
    for client in _build_client(fake_session):
        response = client.get("/v1/sanctuary/me", headers={"Authorization": "Bearer fake"})
    body = response.json()
    assert body["elements"][0]["photo_id"] is None
    assert fake_session.execute.await_count == 5


def test_element_photo_id_none_for_foreign_observation(
    monkeypatch: pytest.MonkeyPatch, fake_session: AsyncMock
) -> None:
    """Defensive ownership pin: the batched observation query filters on
    the CURRENT user's id, so an element pointing at another user's
    observation resolves no photo. The fake returns zero rows (what
    Postgres would produce under that WHERE); the test also asserts the
    filter is really in the emitted SQL."""
    _stub_token_verifier(monkeypatch)
    _wire_session(
        fake_session,
        user=_user(),
        elements=[_element(element_id=_MEADOW_COARSE_PLANTAE)],
        observations=[],  # user_id filter excluded the foreign row
    )
    for client in _build_client(fake_session):
        response = client.get("/v1/sanctuary/me", headers={"Authorization": "Bearer fake"})
    body = response.json()
    assert body["elements"][0]["photo_id"] is None
    # Query #5 (index 4) is the observation lookup; it must pin user_id
    # and read moderation_status.
    observation_call = fake_session.execute.await_args_list[4]
    rendered = str(observation_call.args[0]).lower()
    assert "user_id" in rendered
    assert "moderation_status" in rendered


def test_photo_id_field_is_additive(
    monkeypatch: pytest.MonkeyPatch, fake_session: AsyncMock
) -> None:
    """Existing element fields are untouched; photo_id rides alongside."""
    _stub_token_verifier(monkeypatch)
    _wire_session(
        fake_session,
        user=_user(),
        elements=[_element(element_id=_MEADOW_COARSE_PLANTAE, payload={"iconic_taxon": "Plantae"})],
        observations=[(_SOURCE_OBS_ID, _PHOTO_ID, "clean")],
    )
    for client in _build_client(fake_session):
        response = client.get("/v1/sanctuary/me", headers={"Authorization": "Bearer fake"})
    element = response.json()["elements"][0]
    assert element["title"] == "Plants in the meadow"
    assert element["icon"] == "sanctuary.meadow.plantae"
    assert element["source_observation_id"] == _SOURCE_OBS_ID
    assert element["photo_id"] == _PHOTO_ID


# ---------------------------------------------------------------------------
# Group I: expedition souvenirs (read-time, ADR 0012).
# ---------------------------------------------------------------------------


def test_souvenir_appears_for_completed_expedition(
    monkeypatch: pytest.MonkeyPatch, fake_session: AsyncMock
) -> None:
    _stub_token_verifier(monkeypatch)
    completed_at = datetime(2026, 6, 20, 15, 0, 0, tzinfo=UTC)
    _wire_session(
        fake_session,
        user=_user(),
        progress=[("backyard_starter", completed_at)],
    )
    for client in _build_client(fake_session):
        response = client.get("/v1/sanctuary/me", headers={"Authorization": "Bearer fake"})
    body = response.json()
    assert len(body["souvenirs"]) == 1
    souvenir = body["souvenirs"][0]
    assert souvenir["expedition_id"] == "backyard_starter"
    # Authored zone spread per ADR 0012: the backyard starter souvenir
    # lands in the meadow.
    assert souvenir["zone_id"] == "meadow"
    assert souvenir["icon"] == "sanctuary.souvenir.backyard_starter"
    assert souvenir["title"]
    assert souvenir["detail"]
    assert souvenir["completed_at"] is not None


def test_souvenirs_ordered_by_completed_at(
    monkeypatch: pytest.MonkeyPatch, fake_session: AsyncMock
) -> None:
    _stub_token_verifier(monkeypatch)
    base = datetime(2026, 6, 20, 15, 0, 0, tzinfo=UTC)
    # The route query orders by completed_at ASC; the fake returns rows
    # already in that order and the response must preserve it.
    _wire_session(
        fake_session,
        user=_user(),
        progress=[
            ("park_starter", base),
            ("backyard_starter", base + timedelta(days=2)),
        ],
    )
    for client in _build_client(fake_session):
        response = client.get("/v1/sanctuary/me", headers={"Authorization": "Bearer fake"})
    body = response.json()
    assert [s["expedition_id"] for s in body["souvenirs"]] == [
        "park_starter",
        "backyard_starter",
    ]


def test_in_progress_expedition_yields_no_souvenir(
    monkeypatch: pytest.MonkeyPatch, fake_session: AsyncMock
) -> None:
    """The progress query itself filters to completed rows -- assert the
    NOT NULL predicate is in the emitted SQL, and an empty result shape
    yields an empty shelf."""
    _stub_token_verifier(monkeypatch)
    _wire_session(fake_session, user=_user(), progress=[])
    for client in _build_client(fake_session):
        response = client.get("/v1/sanctuary/me", headers={"Authorization": "Bearer fake"})
    body = response.json()
    assert body["souvenirs"] == []
    # Query #5 (index 4; no elements so no observation lookup) is the
    # expedition_progress SELECT.
    progress_call = fake_session.execute.await_args_list[4]
    rendered = str(progress_call.args[0]).lower()
    assert "completed_at is not null" in rendered
    assert "user_id" in rendered


def test_unknown_expedition_id_in_progress_rows_is_skipped(
    monkeypatch: pytest.MonkeyPatch, fake_session: AsyncMock
) -> None:
    """A completed expedition with no authored souvenir (content trails)
    is silently dropped -- no 500, no fallback entry."""
    _stub_token_verifier(monkeypatch)
    completed_at = datetime(2026, 6, 20, 15, 0, 0, tzinfo=UTC)
    _wire_session(
        fake_session,
        user=_user(),
        progress=[
            ("ghost_expedition_no_souvenir", completed_at),
            ("backyard_starter", completed_at + timedelta(days=1)),
        ],
    )
    for client in _build_client(fake_session):
        response = client.get("/v1/sanctuary/me", headers={"Authorization": "Bearer fake"})
    body = response.json()
    assert [s["expedition_id"] for s in body["souvenirs"]] == ["backyard_starter"]


def test_empty_state_has_no_souvenirs(
    monkeypatch: pytest.MonkeyPatch, fake_session: AsyncMock
) -> None:
    _stub_token_verifier(monkeypatch)
    _wire_session(fake_session, user=_user())
    for client in _build_client(fake_session):
        response = client.get("/v1/sanctuary/me", headers={"Authorization": "Bearer fake"})
    assert response.json()["souvenirs"] == []


def test_content_loader_tolerates_missing_souvenirs_json(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A deployed backend may run against a content tree authored before
    souvenirs.json existed -- the loader must yield an empty souvenir
    index, not raise."""
    import shutil

    import app.sanctuary.content as content_module

    replica = tmp_path / "sanctuary"
    shutil.copytree(content_module._CONTENT_ROOT, replica)
    (replica / "souvenirs.json").unlink()
    monkeypatch.setattr(content_module, "_CONTENT_ROOT", replica)
    reset_sanctuary_content_cache()

    content = content_module.get_sanctuary_content()
    assert content.config.souvenirs == []
    assert content.souvenir_by_expedition_id == {}


def test_current_season_helper_table() -> None:
    """Direct unit coverage of the date-based season selector. Boundaries
    must round-trip exactly so the visual tint flips on the documented
    date and never a day off."""
    from datetime import date as _date

    from app.sanctuary.season import current_season as cs

    cases: list[tuple[_date, str]] = [
        (_date(2026, 3, 1), "spring"),
        (_date(2026, 5, 31), "spring"),
        (_date(2026, 6, 1), "summer"),
        (_date(2026, 8, 31), "summer"),
        (_date(2026, 9, 1), "autumn"),
        (_date(2026, 11, 30), "autumn"),
        (_date(2026, 12, 1), "winter"),
        (_date(2026, 2, 28), "winter"),
        (_date(2027, 1, 15), "winter"),
        (_date(2027, 2, 29 - 1), "winter"),  # avoid actual leap-day type quirk
    ]
    for d, expected in cases:
        assert cs(d) == expected, f"current_season({d}) -> {cs(d)!r}, expected {expected!r}"
