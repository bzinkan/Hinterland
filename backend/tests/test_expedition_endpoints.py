"""Tests for /v1/expeditions/* endpoints."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes import expeditions as expeditions_routes
from app.core.config import Settings
from app.db import models
from app.db.session import get_db_session
from app.main import create_app
from tests.helpers.auth import stub_token_verifier

_FIREBASE_UID = "firebase-kid-001"
_USER_ID = "01J0KIDID0000000000000ULID"
_GROUP_ID = "01J0GROUPID00000000000ULID"


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


def _user() -> models.User:
    return models.User(id=_USER_ID, firebase_uid=_FIREBASE_UID, role="kid", display_name="Kid")


def _exp_body(
    *,
    exp_id: str,
    prerequisites: list[dict[str, object]] | None = None,
    steps_count: int = 1,
) -> dict[str, Any]:
    return {
        "id": exp_id,
        "title": f"Test {exp_id}",
        "tier": 1,
        "duration_minutes": 20,
        "environments": ["yard"],
        "intro": "Find some things.",
        "outro": "Real science.",
        "prerequisites": prerequisites or [],
        "steps": [
            {"id": f"s{i}", "description": "x", "match": {"kind": "any_organism"}}
            for i in range(steps_count)
        ],
    }


def _content(exp_id: str, body: dict[str, Any]) -> models.ExpeditionContent:
    return models.ExpeditionContent(
        id=exp_id, tier=body["tier"], content_hash="x", body=body, archived=False
    )


# ---------------------------------------------------------------------------
# GET /v1/expeditions/available
# ---------------------------------------------------------------------------


def _wire_available(
    fake_session: AsyncMock,
    *,
    user: models.User | None,
    dex_count: int = 0,
    completed_ids: list[str] | None = None,
    any_progress_ids: list[str] | None = None,
    contents: list[models.ExpeditionContent] | None = None,
    region_results: list[list[str]] | None = None,
) -> None:
    user_result = MagicMock()
    user_result.scalar_one_or_none = MagicMock(return_value=user)

    dex_result = MagicMock()
    dex_result.all = MagicMock(return_value=[(dex_count,)])

    completed_result = MagicMock()
    completed_result.all = MagicMock(return_value=[(i,) for i in (completed_ids or [])])

    any_progress_result = MagicMock()
    any_progress_result.all = MagicMock(return_value=[(i,) for i in (any_progress_ids or [])])

    content_result = MagicMock()
    scalars = MagicMock()
    scalars.all = MagicMock(return_value=contents or [])
    content_result.scalars = MagicMock(return_value=scalars)

    side_effects: list[Any] = [user_result]
    if user is not None:
        side_effects.extend([dex_result, completed_result, any_progress_result, content_result])
        # One entry per rarity_cache DISTINCT iconic_taxon query (a
        # geohash-4 hit issues one; the geohash-3 fallback adds a
        # second). Tests without a geohash4 param wire none, so any
        # unexpected region lookup exhausts the side_effect list.
        for values in region_results or []:
            region_result = MagicMock()
            region_scalars = MagicMock()
            region_scalars.all = MagicMock(return_value=values)
            region_result.scalars = MagicMock(return_value=region_scalars)
            side_effects.append(region_result)

    fake_session.execute = AsyncMock(side_effect=side_effects)


def test_available_requires_bearer(fake_session: AsyncMock) -> None:
    for client in _build_client(fake_session):
        response = client.get("/v1/expeditions/available")
        assert response.status_code == 401


def test_available_403_when_no_postgres_user(
    monkeypatch: pytest.MonkeyPatch, fake_session: AsyncMock
) -> None:
    _stub_token_verifier(monkeypatch)
    _wire_available(fake_session, user=None)
    for client in _build_client(fake_session):
        response = client.get("/v1/expeditions/available", headers={"Authorization": "Bearer fake"})
        assert response.status_code == 403


def test_available_returns_unstarted_unblocked_expeditions(
    monkeypatch: pytest.MonkeyPatch, fake_session: AsyncMock
) -> None:
    _stub_token_verifier(monkeypatch)
    contents = [
        _content("a", _exp_body(exp_id="a")),
        _content("b", _exp_body(exp_id="b")),
    ]
    _wire_available(
        fake_session,
        user=_user(),
        dex_count=0,
        completed_ids=[],
        any_progress_ids=[],
        contents=contents,
    )
    for client in _build_client(fake_session):
        response = client.get("/v1/expeditions/available", headers={"Authorization": "Bearer fake"})
        assert response.status_code == 200
        body = response.json()
        assert [item["id"] for item in body["items"]] == ["a", "b"]


def test_available_filters_already_started(
    monkeypatch: pytest.MonkeyPatch, fake_session: AsyncMock
) -> None:
    _stub_token_verifier(monkeypatch)
    contents = [
        _content("a", _exp_body(exp_id="a")),
        _content("b", _exp_body(exp_id="b")),
    ]
    _wire_available(
        fake_session,
        user=_user(),
        any_progress_ids=["a"],  # started, so not available
        contents=contents,
    )
    for client in _build_client(fake_session):
        response = client.get("/v1/expeditions/available", headers={"Authorization": "Bearer fake"})
        assert [i["id"] for i in response.json()["items"]] == ["b"]


def test_available_filters_unmet_dex_count_prereq(
    monkeypatch: pytest.MonkeyPatch, fake_session: AsyncMock
) -> None:
    _stub_token_verifier(monkeypatch)
    contents = [
        _content(
            "needs_5",
            _exp_body(
                exp_id="needs_5",
                prerequisites=[{"kind": "dex_count_at_least", "value": 5}],
            ),
        )
    ]
    _wire_available(fake_session, user=_user(), dex_count=2, contents=contents)
    for client in _build_client(fake_session):
        response = client.get("/v1/expeditions/available", headers={"Authorization": "Bearer fake"})
        assert response.json()["items"] == []


def test_available_filters_unmet_completed_prereq(
    monkeypatch: pytest.MonkeyPatch, fake_session: AsyncMock
) -> None:
    _stub_token_verifier(monkeypatch)
    contents = [
        _content(
            "sequel",
            _exp_body(
                exp_id="sequel",
                prerequisites=[{"kind": "completed_expedition", "value": "backyard_starter"}],
            ),
        )
    ]
    _wire_available(fake_session, user=_user(), completed_ids=[], contents=contents)
    for client in _build_client(fake_session):
        response = client.get("/v1/expeditions/available", headers={"Authorization": "Bearer fake"})
        assert response.json()["items"] == []


def test_available_valid_geohash4_reorders_by_relevance(
    monkeypatch: pytest.MonkeyPatch, fake_session: AsyncMock
) -> None:
    """The region reports insects but no birds, so the bucket flips the
    (tier, id) order: "b" (insects, great_here) rises above "a" (birds,
    tricky_here) -- but "a" stays listed (downrank, never hide)."""
    _stub_token_verifier(monkeypatch)
    body_a = _exp_body(exp_id="a")
    body_a["steps"][0]["match"] = {"kind": "iconic_taxon", "value": "Aves"}
    body_b = _exp_body(exp_id="b")
    body_b["steps"][0]["match"] = {"kind": "iconic_taxon", "value": "Insecta"}
    _wire_available(
        fake_session,
        user=_user(),
        contents=[_content("a", body_a), _content("b", body_b)],
        region_results=[["Insecta"]],  # geohash-4 hit, no fallback query
    )
    for client in _build_client(fake_session):
        response = client.get(
            "/v1/expeditions/available",
            params={"geohash4": "9q5c"},
            headers={"Authorization": "Bearer fake"},
        )
        assert response.status_code == 200
        items = response.json()["items"]
        assert [i["id"] for i in items] == ["b", "a"]
        assert items[0]["relevance"] == {
            "level": "great_here",
            "reason": "People spot insects near you a lot",
        }
        assert items[1]["relevance"] == {
            "level": "tricky_here",
            "reason": "Birds are rarely reported near you -- this one is a challenge",
        }


def test_available_invalid_geohash4_is_silently_ignored(
    monkeypatch: pytest.MonkeyPatch, fake_session: AsyncMock
) -> None:
    """A geohash4 of "ailo" fails the base32 alphabet, so an old or
    buggy client gets exactly the unranked list -- never a 422. No
    region query is wired, so any lookup attempt would exhaust the
    mock."""
    _stub_token_verifier(monkeypatch)
    body_a = _exp_body(exp_id="a")
    body_a["steps"][0]["match"] = {"kind": "iconic_taxon", "value": "Aves"}
    contents = [_content("a", body_a), _content("b", _exp_body(exp_id="b"))]
    _wire_available(fake_session, user=_user(), contents=contents)
    for client in _build_client(fake_session):
        response = client.get(
            "/v1/expeditions/available",
            params={"geohash4": "ailo"},
            headers={"Authorization": "Bearer fake"},
        )
        assert response.status_code == 200
        items = response.json()["items"]
        assert [i["id"] for i in items] == ["a", "b"]
        assert all(i["relevance"] == {"level": "unknown", "reason": None} for i in items)


def test_available_without_geohash4_keeps_order_and_unknown_relevance(
    monkeypatch: pytest.MonkeyPatch, fake_session: AsyncMock
) -> None:
    """No param -> exactly today's (tier, id) order, relevance additive
    and unknown, and no region query issued."""
    _stub_token_verifier(monkeypatch)
    body_a = _exp_body(exp_id="a")
    body_a["steps"][0]["match"] = {"kind": "iconic_taxon", "value": "Aves"}
    contents = [_content("a", body_a), _content("b", _exp_body(exp_id="b"))]
    _wire_available(fake_session, user=_user(), contents=contents)
    for client in _build_client(fake_session):
        response = client.get("/v1/expeditions/available", headers={"Authorization": "Bearer fake"})
        assert response.status_code == 200
        items = response.json()["items"]
        assert [i["id"] for i in items] == ["a", "b"]
        assert all(i["relevance"] == {"level": "unknown", "reason": None} for i in items)


def test_available_ranked_log_fields_and_cold_start_keeps_order(
    monkeypatch: pytest.MonkeyPatch, fake_session: AsyncMock
) -> None:
    """The ranking log carries only {geohash4, ranked, region_known} --
    never lat/lng. Cold start (both region queries empty) keeps the
    original order with unknown relevance; the geohash is lowercased
    before use so the raw "9Q5C" is never logged."""
    _stub_token_verifier(monkeypatch)
    fake_log = MagicMock()
    monkeypatch.setattr(expeditions_routes, "log", fake_log)
    contents = [_content("a", _exp_body(exp_id="a")), _content("b", _exp_body(exp_id="b"))]
    _wire_available(
        fake_session,
        user=_user(),
        contents=contents,
        region_results=[[], []],  # geohash-4 empty, geohash-3 fallback empty
    )
    for client in _build_client(fake_session):
        response = client.get(
            "/v1/expeditions/available",
            params={"geohash4": "9Q5C"},
            headers={"Authorization": "Bearer fake"},
        )
        assert response.status_code == 200
        items = response.json()["items"]
        assert [i["id"] for i in items] == ["a", "b"]
        assert all(i["relevance"] == {"level": "unknown", "reason": None} for i in items)

    ranked_calls = [
        call
        for call in fake_log.info.call_args_list
        if call.args and call.args[0] == "expeditions.available.ranked"
    ]
    assert len(ranked_calls) == 1
    assert ranked_calls[0].kwargs == {"geohash4": "9q5c", "ranked": True, "region_known": False}


# ---------------------------------------------------------------------------
# GET /v1/expeditions/me
# ---------------------------------------------------------------------------


def _wire_me(
    fake_session: AsyncMock,
    *,
    user: models.User | None,
    rows: list[tuple[models.ExpeditionProgress, models.ExpeditionContent]] | None = None,
) -> None:
    user_result = MagicMock()
    user_result.scalar_one_or_none = MagicMock(return_value=user)

    rows_result = MagicMock()
    rows_result.all = MagicMock(return_value=rows or [])

    side_effects: list[Any] = [user_result]
    if user is not None:
        side_effects.append(rows_result)

    fake_session.execute = AsyncMock(side_effect=side_effects)


def _progress_row(
    exp_id: str,
    *,
    completed_steps: dict[str, Any],
    completed_at: datetime | None = None,
    focused_at: datetime | None = None,
) -> models.ExpeditionProgress:
    progress = models.ExpeditionProgress(
        id=f"prog-{exp_id}",
        user_id=_USER_ID,
        group_id=_GROUP_ID,
        expedition_id=exp_id,
        completed_steps=completed_steps,
        completed_at=completed_at,
        focused_at=focused_at,
    )
    progress.created_at = datetime(2026, 5, 10, 12, 0, 0, tzinfo=UTC)
    return progress


def test_me_requires_bearer(fake_session: AsyncMock) -> None:
    for client in _build_client(fake_session):
        response = client.get("/v1/expeditions/me")
        assert response.status_code == 401


def test_me_returns_step_detail_with_mixed_completion_formats(
    monkeypatch: pytest.MonkeyPatch, fake_session: AsyncMock
) -> None:
    """Steps come back in content order with description/hint, and
    completed_at resolves for BOTH stored value formats (new dict +
    legacy plain string)."""
    _stub_token_verifier(monkeypatch)
    body = _exp_body(exp_id="backyard_starter", steps_count=3)
    body["subtitle"] = "Start here"
    body["steps"][0]["hint"] = "look down"
    progress = _progress_row(
        "backyard_starter",
        completed_steps={
            # New dict format ...
            "s0": {
                "completed_at": "2026-05-10T13:00:00+00:00",
                "observation_id": "01J0OBSID0000000000000ULID",
            },
            # ... and a legacy plain-string row.
            "s1": "2026-05-10T14:00:00+00:00",
        },
    )
    _wire_me(
        fake_session,
        user=_user(),
        rows=[(progress, _content("backyard_starter", body))],
    )
    for client in _build_client(fake_session):
        response = client.get("/v1/expeditions/me", headers={"Authorization": "Bearer fake"})
        assert response.status_code == 200
        payload = response.json()
        assert payload["active_expedition_id"] == "backyard_starter"
        items = payload["items"]
        assert len(items) == 1
        item = items[0]
        assert item["expedition_id"] == "backyard_starter"
        assert item["title"] == "Test backyard_starter"
        assert item["subtitle"] == "Start here"
        assert item["intro"] == "Find some things."
        assert item["outro"] == "Real science."
        assert item["completed_step_count"] == 2
        assert item["total_step_count"] == 3
        assert item["focused_at"] is None
        steps = item["steps"]
        assert [s["id"] for s in steps] == ["s0", "s1", "s2"]
        assert steps[0]["description"] == "x"
        assert steps[0]["hint"] == "look down"
        assert steps[1]["hint"] is None
        assert steps[0]["completed_at"].startswith("2026-05-10T13:00:00")
        assert steps[1]["completed_at"].startswith("2026-05-10T14:00:00")
        assert steps[2]["completed_at"] is None


def test_me_malformed_completed_at_degrades_to_null(
    monkeypatch: pytest.MonkeyPatch, fake_session: AsyncMock
) -> None:
    """A garbage completed_at string in a stored row must not 500 /me."""
    _stub_token_verifier(monkeypatch)
    body = _exp_body(exp_id="x")
    progress = _progress_row("x", completed_steps={"s0": "not-a-timestamp"})
    _wire_me(fake_session, user=_user(), rows=[(progress, _content("x", body))])
    for client in _build_client(fake_session):
        response = client.get("/v1/expeditions/me", headers={"Authorization": "Bearer fake"})
        assert response.status_code == 200
        payload = response.json()
        assert payload["active_expedition_id"] == "x"
        item = payload["items"][0]
        # The count still reflects the stored key; the step itself
        # degrades to "not completed".
        assert item["completed_step_count"] == 1
        assert item["steps"][0]["completed_at"] is None


def test_me_bad_content_falls_back_to_empty_detail(
    monkeypatch: pytest.MonkeyPatch, fake_session: AsyncMock
) -> None:
    _stub_token_verifier(monkeypatch)
    content = models.ExpeditionContent(
        id="x", tier=1, content_hash="x", body={"not": "valid"}, archived=False
    )
    progress = _progress_row("x", completed_steps={})
    _wire_me(fake_session, user=_user(), rows=[(progress, content)])
    for client in _build_client(fake_session):
        response = client.get("/v1/expeditions/me", headers={"Authorization": "Bearer fake"})
        assert response.status_code == 200
        payload = response.json()
        assert payload["active_expedition_id"] == "x"
        item = payload["items"][0]
        assert item["title"] == "x"
        assert item["subtitle"] is None
        assert item["intro"] == ""
        assert item["outro"] == ""
        assert item["steps"] == []
        assert item["total_step_count"] == 0


def test_me_prefers_focused_incomplete_expedition(
    monkeypatch: pytest.MonkeyPatch, fake_session: AsyncMock
) -> None:
    _stub_token_verifier(monkeypatch)
    older_focus = datetime(2026, 5, 10, 12, 15, 0, tzinfo=UTC)
    newest = _progress_row("newest", completed_steps={})
    focused = _progress_row("focused", completed_steps={}, focused_at=older_focus)
    newest.created_at = datetime(2026, 5, 10, 13, 0, 0, tzinfo=UTC)
    focused.created_at = datetime(2026, 5, 10, 12, 0, 0, tzinfo=UTC)
    _wire_me(
        fake_session,
        user=_user(),
        rows=[
            (newest, _content("newest", _exp_body(exp_id="newest"))),
            (focused, _content("focused", _exp_body(exp_id="focused"))),
        ],
    )
    for client in _build_client(fake_session):
        response = client.get("/v1/expeditions/me", headers={"Authorization": "Bearer fake"})
        assert response.status_code == 200
        payload = response.json()
        assert payload["active_expedition_id"] == "focused"
        assert payload["items"][1]["focused_at"].startswith("2026-05-10T12:15:00")


def test_me_ignores_completed_focus_for_active_expedition(
    monkeypatch: pytest.MonkeyPatch, fake_session: AsyncMock
) -> None:
    _stub_token_verifier(monkeypatch)
    completed = _progress_row(
        "done",
        completed_steps={"s0": "2026-05-10T13:00:00+00:00"},
        completed_at=datetime(2026, 5, 10, 13, 0, 0, tzinfo=UTC),
        focused_at=datetime(2026, 5, 10, 12, 15, 0, tzinfo=UTC),
    )
    active = _progress_row("active", completed_steps={})
    _wire_me(
        fake_session,
        user=_user(),
        rows=[
            (completed, _content("done", _exp_body(exp_id="done"))),
            (active, _content("active", _exp_body(exp_id="active"))),
        ],
    )
    for client in _build_client(fake_session):
        response = client.get("/v1/expeditions/me", headers={"Authorization": "Bearer fake"})
        assert response.status_code == 200
        assert response.json()["active_expedition_id"] == "active"


# ---------------------------------------------------------------------------
# POST /v1/expeditions/{id}/start
# ---------------------------------------------------------------------------


def _wire_start(
    fake_session: AsyncMock,
    *,
    user: models.User | None,
    content: models.ExpeditionContent | None,
    dex_count: int = 0,
    completed_ids: list[str] | None = None,
    existing_progress_id: str | None = None,
) -> None:
    user_result = MagicMock()
    user_result.scalar_one_or_none = MagicMock(return_value=user)

    content_result = MagicMock()
    content_result.scalar_one_or_none = MagicMock(return_value=content)

    dex_result = MagicMock()
    dex_result.all = MagicMock(return_value=[(dex_count,)])

    completed_result = MagicMock()
    completed_result.all = MagicMock(return_value=[(i,) for i in (completed_ids or [])])

    existing_result = MagicMock()
    existing_result.scalar_one_or_none = MagicMock(return_value=existing_progress_id)

    focus_update_result = MagicMock()

    side_effects: list[Any] = [user_result]
    if user is not None:
        side_effects.append(content_result)
        if content is not None:
            side_effects.extend([dex_result, completed_result, existing_result])
            if existing_progress_id is None:
                side_effects.append(focus_update_result)

    fake_session.execute = AsyncMock(side_effect=side_effects)
    fake_session.add = MagicMock()
    fake_session.commit = AsyncMock()
    fake_session.refresh = AsyncMock(
        side_effect=lambda obj: setattr(
            obj, "created_at", datetime(2026, 5, 10, 12, 0, 0, tzinfo=UTC)
        )
    )


def test_start_requires_bearer(fake_session: AsyncMock) -> None:
    for client in _build_client(fake_session):
        response = client.post("/v1/expeditions/x/start")
        assert response.status_code == 401


def test_start_404_when_expedition_missing(
    monkeypatch: pytest.MonkeyPatch, fake_session: AsyncMock
) -> None:
    _stub_token_verifier(monkeypatch)
    _wire_start(fake_session, user=_user(), content=None)
    for client in _build_client(fake_session):
        response = client.post(
            "/v1/expeditions/missing/start",
            headers={"Authorization": "Bearer fake"},
        )
        assert response.status_code == 404


def test_start_409_when_prereq_unmet(
    monkeypatch: pytest.MonkeyPatch, fake_session: AsyncMock
) -> None:
    _stub_token_verifier(monkeypatch)
    body = _exp_body(exp_id="x", prerequisites=[{"kind": "dex_count_at_least", "value": 5}])
    _wire_start(fake_session, user=_user(), content=_content("x", body), dex_count=0)
    for client in _build_client(fake_session):
        response = client.post("/v1/expeditions/x/start", headers={"Authorization": "Bearer fake"})
        assert response.status_code == 409
        assert "Prerequisites" in response.json()["error"]["message"]


def test_start_409_when_already_started(
    monkeypatch: pytest.MonkeyPatch, fake_session: AsyncMock
) -> None:
    _stub_token_verifier(monkeypatch)
    body = _exp_body(exp_id="x")
    _wire_start(
        fake_session,
        user=_user(),
        content=_content("x", body),
        existing_progress_id="some-id",
    )
    for client in _build_client(fake_session):
        response = client.post("/v1/expeditions/x/start", headers={"Authorization": "Bearer fake"})
        assert response.status_code == 409
        assert "already started" in response.json()["error"]["message"]


def test_start_happy_path_creates_progress_row(
    monkeypatch: pytest.MonkeyPatch, fake_session: AsyncMock
) -> None:
    _stub_token_verifier(monkeypatch)
    body = _exp_body(exp_id="backyard_starter")
    _wire_start(fake_session, user=_user(), content=_content("backyard_starter", body))
    for client in _build_client(fake_session):
        response = client.post(
            "/v1/expeditions/backyard_starter/start",
            headers={"Authorization": "Bearer fake"},
        )
        assert response.status_code == 201
        body_json = response.json()
        assert body_json["expedition_id"] == "backyard_starter"

    fake_session.add.assert_called_once()
    progress: models.ExpeditionProgress = fake_session.add.call_args.args[0]
    assert isinstance(progress, models.ExpeditionProgress)
    assert progress.user_id == _USER_ID
    assert progress.group_id == _GROUP_ID
    assert progress.expedition_id == "backyard_starter"
    assert progress.completed_steps == {}
    assert progress.focused_at is not None


def test_start_concurrent_duplicate_surfaces_409(
    monkeypatch: pytest.MonkeyPatch, fake_session: AsyncMock
) -> None:
    """Two concurrent starts both pass the pre-check SELECT; the loser's
    commit trips uq_expedition_progress_user_exp and must surface the
    documented 409, not a 500."""
    _stub_token_verifier(monkeypatch)
    body = _exp_body(exp_id="x")
    _wire_start(fake_session, user=_user(), content=_content("x", body))
    fake_session.commit = AsyncMock(
        side_effect=IntegrityError("INSERT", {}, Exception("duplicate key"))
    )
    fake_session.rollback = AsyncMock()
    for client in _build_client(fake_session):
        response = client.post("/v1/expeditions/x/start", headers={"Authorization": "Bearer fake"})
        assert response.status_code == 409
        assert "already started" in response.json()["error"]["message"]

    fake_session.rollback.assert_awaited_once()


# ---------------------------------------------------------------------------
# POST /v1/expeditions/{id}/restart
#
# NOTE: restart's progress SELECT carries .with_for_update(); on a mocked
# session that's transparent, so the row-lock behavior (restart-vs-dispatch
# lost update) is not provable here -- real-Postgres coverage is the
# Phase-11 harness item.
# ---------------------------------------------------------------------------


def _wire_restart(
    fake_session: AsyncMock,
    *,
    user: models.User | None,
    content: models.ExpeditionContent | None,
    progress: models.ExpeditionProgress | None,
) -> None:
    user_result = MagicMock()
    user_result.scalar_one_or_none = MagicMock(return_value=user)

    content_result = MagicMock()
    content_result.scalar_one_or_none = MagicMock(return_value=content)

    progress_result = MagicMock()
    progress_result.scalar_one_or_none = MagicMock(return_value=progress)

    focus_update_result = MagicMock()

    side_effects: list[Any] = [user_result]
    if user is not None:
        side_effects.append(content_result)
        if content is not None:
            side_effects.append(progress_result)
            if progress is not None and progress.completed_at is None:
                side_effects.append(focus_update_result)

    fake_session.execute = AsyncMock(side_effect=side_effects)
    fake_session.commit = AsyncMock()
    fake_session.refresh = AsyncMock()


def test_restart_requires_bearer(fake_session: AsyncMock) -> None:
    for client in _build_client(fake_session):
        response = client.post("/v1/expeditions/x/restart")
        assert response.status_code == 401


def test_restart_happy_path_resets_progress_in_place(
    monkeypatch: pytest.MonkeyPatch, fake_session: AsyncMock
) -> None:
    _stub_token_verifier(monkeypatch)
    body = _exp_body(exp_id="backyard_starter", steps_count=2)
    progress = _progress_row(
        "backyard_starter",
        completed_steps={
            "s0": {
                "completed_at": "2026-05-10T13:00:00+00:00",
                "observation_id": "01J0OBSID0000000000000ULID",
            },
        },
    )
    old_created_at = progress.created_at
    _wire_restart(
        fake_session,
        user=_user(),
        content=_content("backyard_starter", body),
        progress=progress,
    )
    for client in _build_client(fake_session):
        response = client.post(
            "/v1/expeditions/backyard_starter/restart",
            headers={"Authorization": "Bearer fake"},
        )
        assert response.status_code == 200
        assert response.json()["expedition_id"] == "backyard_starter"

    # The row is reset in place: empty step map, no completion, and
    # created_at re-anchored on the fresh run. `progress` is a real
    # instrumented ORM instance, so the endpoint's flag_modified call
    # (which raises on an unmapped attribute) is exercised too.
    assert progress.completed_steps == {}
    assert progress.completed_at is None
    assert progress.created_at > old_created_at
    assert progress.focused_at == progress.created_at
    fake_session.commit.assert_awaited_once()


def test_restart_404_when_never_started(
    monkeypatch: pytest.MonkeyPatch, fake_session: AsyncMock
) -> None:
    _stub_token_verifier(monkeypatch)
    body = _exp_body(exp_id="x")
    _wire_restart(fake_session, user=_user(), content=_content("x", body), progress=None)
    for client in _build_client(fake_session):
        response = client.post(
            "/v1/expeditions/x/restart",
            headers={"Authorization": "Bearer fake"},
        )
        assert response.status_code == 404
        assert "not started" in response.json()["error"]["message"]


def test_restart_404_when_content_archived(
    monkeypatch: pytest.MonkeyPatch, fake_session: AsyncMock
) -> None:
    """The content query filters on archived=false, so an archived
    expedition comes back as no row -- same 404 as a missing one."""
    _stub_token_verifier(monkeypatch)
    _wire_restart(fake_session, user=_user(), content=None, progress=None)
    for client in _build_client(fake_session):
        response = client.post(
            "/v1/expeditions/x/restart",
            headers={"Authorization": "Bearer fake"},
        )
        assert response.status_code == 404
        assert "not found" in response.json()["error"]["message"]


def test_restart_409_when_already_completed(
    monkeypatch: pytest.MonkeyPatch, fake_session: AsyncMock
) -> None:
    """Completed expeditions are trophies (and completed_expedition
    prerequisites hang off them) -- never restartable."""
    _stub_token_verifier(monkeypatch)
    body = _exp_body(exp_id="x")
    progress = _progress_row(
        "x",
        completed_steps={"s0": "2026-05-10T13:00:00+00:00"},
        completed_at=datetime(2026, 5, 10, 13, 0, 0, tzinfo=UTC),
    )
    _wire_restart(fake_session, user=_user(), content=_content("x", body), progress=progress)
    for client in _build_client(fake_session):
        response = client.post(
            "/v1/expeditions/x/restart",
            headers={"Authorization": "Bearer fake"},
        )
        assert response.status_code == 409
        assert "already completed" in response.json()["error"]["message"]

    # Nothing touched on the trophy row.
    assert progress.completed_steps == {"s0": "2026-05-10T13:00:00+00:00"}
    fake_session.commit.assert_not_awaited()


# ---------------------------------------------------------------------------
# POST /v1/expeditions/{id}/focus
# ---------------------------------------------------------------------------


def _wire_focus(
    fake_session: AsyncMock,
    *,
    user: models.User | None,
    progress: models.ExpeditionProgress | None,
) -> None:
    user_result = MagicMock()
    user_result.scalar_one_or_none = MagicMock(return_value=user)

    progress_result = MagicMock()
    progress_result.scalar_one_or_none = MagicMock(return_value=progress)

    focus_update_result = MagicMock()

    side_effects: list[Any] = [user_result]
    if user is not None:
        side_effects.append(progress_result)
        if progress is not None and progress.completed_at is None:
            side_effects.append(focus_update_result)

    fake_session.execute = AsyncMock(side_effect=side_effects)
    fake_session.commit = AsyncMock()


def test_focus_requires_bearer(fake_session: AsyncMock) -> None:
    for client in _build_client(fake_session):
        response = client.post("/v1/expeditions/x/focus")
        assert response.status_code == 401


def test_focus_404_when_never_started(
    monkeypatch: pytest.MonkeyPatch, fake_session: AsyncMock
) -> None:
    _stub_token_verifier(monkeypatch)
    _wire_focus(fake_session, user=_user(), progress=None)
    for client in _build_client(fake_session):
        response = client.post("/v1/expeditions/x/focus", headers={"Authorization": "Bearer fake"})
        assert response.status_code == 404
        assert "not started" in response.json()["error"]["message"]


def test_focus_409_when_completed(monkeypatch: pytest.MonkeyPatch, fake_session: AsyncMock) -> None:
    _stub_token_verifier(monkeypatch)
    progress = _progress_row(
        "x",
        completed_steps={"s0": "2026-05-10T13:00:00+00:00"},
        completed_at=datetime(2026, 5, 10, 13, 0, 0, tzinfo=UTC),
    )
    _wire_focus(fake_session, user=_user(), progress=progress)
    for client in _build_client(fake_session):
        response = client.post("/v1/expeditions/x/focus", headers={"Authorization": "Bearer fake"})
        assert response.status_code == 409
        assert "cannot be focused" in response.json()["error"]["message"]
    fake_session.commit.assert_not_awaited()


def test_focus_happy_path_sets_focus(
    monkeypatch: pytest.MonkeyPatch, fake_session: AsyncMock
) -> None:
    _stub_token_verifier(monkeypatch)
    progress = _progress_row("x", completed_steps={})
    _wire_focus(fake_session, user=_user(), progress=progress)
    for client in _build_client(fake_session):
        response = client.post("/v1/expeditions/x/focus", headers={"Authorization": "Bearer fake"})
        assert response.status_code == 200
        body = response.json()
        assert body["expedition_id"] == "x"
        assert body["focused_at"] is not None

    assert progress.focused_at is not None
    fake_session.commit.assert_awaited_once()
