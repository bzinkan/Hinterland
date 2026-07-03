"""Tests for PATCH /v1/observations/{id} (Phase 7 slice 3)."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.db import models
from app.db.session import get_db_session
from app.dispatcher.types import HandlerResult, Reward
from app.main import create_app
from app.services.species_cache import CachedSpecies
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


def _obs_row(
    *,
    taxon_id: int | None = None,
    taxon_first_assigned_at: datetime | None = None,
    dispatched_at: datetime | None = None,
) -> models.Observation:
    obs = models.Observation(
        id=_OBS_ID,
        user_id=_USER_ID,
        group_id=_GROUP_ID,
        photo_id="01J0PHOTOID00000000000ULID",
        latitude=39.1,
        longitude=-84.5,
        taxon_id=taxon_id,
        species_name=None,
        place_name=None,
        taxon_first_assigned_at=taxon_first_assigned_at,
        dispatched_at=dispatched_at,
    )
    return obs


def _photo_row() -> models.Photo:
    return models.Photo(
        id="01J0PHOTOID00000000000ULID",
        user_id=_USER_ID,
        bucket="dragonfly-photos-test",
        object_name="pending/01J0PHOTOID00000000000ULID.jpg",
        status="pending",
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
    obs: models.Observation | None = None,
    species_cache_hit: models.SpeciesCache | None = None,
    dispatch_minted_dex_id: str | None = None,
    dispatch_photo: models.Photo | None = None,
    dispatch_group: models.Group | None = None,
) -> None:
    """Wire up the user lookup, observation lookup, and (optionally) the
    species cache lookup -- in that order, matching the route's call sites.

    Tests whose payload assigns the observation's first taxon also hit the
    re-dispatch block, which probes for a dex entry minted by this
    observation before dispatching. Pass `dispatch_minted_dex_id` to stage
    a probe hit (dispatch skipped), or `dispatch_photo` (+ optionally
    `dispatch_group`) to stage a probe miss followed by the Photo and
    Group lookups the dispatch path issues.
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
        if dispatch_photo is not None or dispatch_minted_dex_id is not None:
            # The dex-mint probe runs FIRST on a first taxon assignment,
            # before any state is written -- a probe-blocked assignment
            # must not clear dispatched_at for the replay job.
            probe_result = MagicMock()
            probe_result.scalar_one_or_none = MagicMock(return_value=dispatch_minted_dex_id)
            side_effects.append(probe_result)
        if obs is not None and species_cache_hit is not None:
            # Species cache lookup happens when taxon_id is set without
            # an explicit species_name, or on any first taxon assignment
            # (cache warm for the re-dispatch). Tests that don't go down
            # this path -- or that patch get_or_fill directly -- don't
            # pre-stage a species result.
            side_effects.append(species_result)
        if dispatch_photo is not None:
            photo_result = MagicMock()
            photo_result.scalar_one = MagicMock(return_value=dispatch_photo)
            group_result = MagicMock()
            group_result.scalar_one_or_none = MagicMock(return_value=dispatch_group)
            side_effects.extend([photo_result, group_result])
            # One spare result for the failure-path dispatched_at reset
            # UPDATE. Harmless leftover when the dispatch succeeds.
            side_effects.append(MagicMock())

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


def test_patch_taxon_with_explicit_species_name_warms_cache_keeps_name(
    monkeypatch: pytest.MonkeyPatch,
    patch_client: TestClient,
    fake_session: AsyncMock,
) -> None:
    """When the caller sends both, the name is honored verbatim but the
    cache is STILL warmed: a first taxon assignment re-dispatches, and
    the iconic/descendant matchers need the cached iNat payload
    (ancestor_ids) -- a cold cache here would silently miss forever
    because dispatched_at then stamps."""
    _stub_token_verifier(monkeypatch)
    # First taxon assignment also hits the re-dispatch block; stage a
    # dex-mint probe hit so it skips cleanly (re-dispatch behavior has
    # its own tests below).
    _wire_session(
        fake_session,
        user=_user_row(),
        obs=_obs_row(),
        dispatch_minted_dex_id="01JDEXROW00000000000000ULID",
    )
    warm_mock = AsyncMock(
        return_value=CachedSpecies(
            taxon_id=12345,
            scientific_name="Cardinalis cardinalis",
            common_name="Northern Cardinal",
            iconic_taxon="Aves",
            ancestor_ids=(1, 2, 3),
        )
    )
    monkeypatch.setattr("app.api.routes.observations.species_cache.get_or_fill", warm_mock)

    response = patch_client.patch(
        f"/v1/observations/{_OBS_ID}",
        json={"taxon_id": 12345, "species_name": "Cardinal (my pick)"},
        headers={"Authorization": "Bearer fake"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["taxon_id"] == 12345
    # Caller's name wins verbatim -- the cache result never overwrites it.
    assert body["species_name"] == "Cardinal (my pick)"
    warm_mock.assert_awaited_once()


def test_patch_taxon_only_fills_species_name_from_cache_hit(
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
    # First taxon assignment also hits the re-dispatch block; stage a
    # dex-mint probe hit so it skips cleanly (re-dispatch behavior has
    # its own tests below).
    _wire_session(
        fake_session,
        user=_user_row(),
        obs=_obs_row(),
        species_cache_hit=cached,
        dispatch_minted_dex_id="01JDEXROW00000000000000ULID",
    )

    response = patch_client.patch(
        f"/v1/observations/{_OBS_ID}",
        json={"taxon_id": 12345},
        headers={"Authorization": "Bearer fake"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["taxon_id"] == 12345
    assert body["species_name"] == "Northern Cardinal"


# ---------------------------------------------------------------------------
# Taxon-transition re-dispatch
# ---------------------------------------------------------------------------


def _cached_species() -> models.SpeciesCache:
    return models.SpeciesCache(
        taxon_id=12345,
        scientific_name="Cardinalis cardinalis",
        common_name="Northern Cardinal",
        iconic_taxon="Aves",
        source_payload={},
    )


def test_patch_new_taxon_dispatches_and_returns_rewards(
    monkeypatch: pytest.MonkeyPatch,
    patch_client: TestClient,
    fake_session: AsyncMock,
) -> None:
    """Setting a taxon on a taxon-less observation (the live mobile flow)
    re-runs the dispatcher and surfaces its rewards on the PATCH."""
    _stub_token_verifier(monkeypatch)
    obs = _obs_row()
    _wire_session(
        fake_session,
        user=_user_row(),
        obs=obs,
        species_cache_hit=_cached_species(),
        dispatch_photo=_photo_row(),
        dispatch_group=_group_row(),
    )
    reward = Reward(
        type="expedition_step",
        title="Expedition step!",
        detail="Backyard Starter: Find a bird",
        icon="expedition.step",
        weight=40,
        payload={"expedition_id": "backyard_starter", "step_id": "bird"},
    )

    # The route only re-stamps dispatched_at when WorldHandler reported
    # health, so the fake dispatch must populate world state the way the
    # real dispatcher does.
    async def fake_dispatch(ctx: Any, handlers: Any) -> list[Reward]:
        ctx.results["world"] = HandlerResult(
            rewards=[], state={"contribution_id": _OBS_ID, "replay": False}
        )
        return [reward]

    dispatch_mock = AsyncMock(side_effect=fake_dispatch)
    monkeypatch.setattr("app.api.routes.observations.dispatch", dispatch_mock)

    response = patch_client.patch(
        f"/v1/observations/{_OBS_ID}",
        json={"taxon_id": 12345},
        headers={"Authorization": "Bearer fake"},
    )
    assert response.status_code == 200
    body = response.json()
    assert [r["type"] for r in body["rewards"]] == ["expedition_step"]

    dispatch_mock.assert_awaited_once()
    ctx = dispatch_mock.await_args.args[0]
    assert ctx.observation is obs
    assert obs.dispatched_at is not None
    # The write-once marker rode the same commit as the taxon itself.
    assert obs.taxon_first_assigned_at is not None
    # Two commits: the field patch, then the dispatched_at stamp.
    assert fake_session.commit.await_count == 2


def test_patch_clear_then_repick_does_not_dispatch_again(
    monkeypatch: pytest.MonkeyPatch,
    patch_client: TestClient,
    fake_session: AsyncMock,
) -> None:
    """The write-once marker closes the clear-and-repick loophole: a
    taxonless observation that HAS dispatched before (taxon assigned,
    then cleared via raw API) must not dispatch a second time with a
    different taxon -- one photo earns one round of rewards."""
    _stub_token_verifier(monkeypatch)
    marker = datetime(2026, 7, 1, 12, 0, 0, tzinfo=UTC)
    stamp = datetime(2026, 7, 1, 12, 0, 5, tzinfo=UTC)
    obs = _obs_row(
        taxon_id=None,
        taxon_first_assigned_at=marker,
        dispatched_at=stamp,
    )
    _wire_session(
        fake_session,
        user=_user_row(),
        obs=obs,
        species_cache_hit=_cached_species(),
    )
    dispatch_mock = AsyncMock(return_value=[])
    monkeypatch.setattr("app.api.routes.observations.dispatch", dispatch_mock)

    response = patch_client.patch(
        f"/v1/observations/{_OBS_ID}",
        json={"taxon_id": 99999},
        headers={"Authorization": "Bearer fake"},
    )
    assert response.status_code == 200
    assert response.json()["rewards"] == []
    dispatch_mock.assert_not_awaited()
    # Mutation-proofing: if the marker gate were dropped, the route would
    # re-stamp the marker, clear dispatched_at (queueing a probe-less
    # replay dispatch), and take the error path's extra commits. Pin all
    # three so removing the gate fails loudly.
    assert obs.taxon_first_assigned_at == marker
    assert obs.dispatched_at == stamp
    fake_session.commit.assert_awaited_once()
    fake_session.rollback.assert_not_awaited()


def test_patch_same_taxon_id_does_not_dispatch(
    monkeypatch: pytest.MonkeyPatch,
    patch_client: TestClient,
    fake_session: AsyncMock,
) -> None:
    _stub_token_verifier(monkeypatch)
    _wire_session(
        fake_session,
        user=_user_row(),
        obs=_obs_row(taxon_id=12345),
        species_cache_hit=_cached_species(),
    )
    dispatch_mock = AsyncMock(return_value=[])
    monkeypatch.setattr("app.api.routes.observations.dispatch", dispatch_mock)

    response = patch_client.patch(
        f"/v1/observations/{_OBS_ID}",
        json={"taxon_id": 12345},
        headers={"Authorization": "Bearer fake"},
    )
    assert response.status_code == 200
    assert response.json()["rewards"] == []
    dispatch_mock.assert_not_awaited()


def test_patch_taxon_correction_does_not_dispatch(
    monkeypatch: pytest.MonkeyPatch,
    patch_client: TestClient,
    fake_session: AsyncMock,
) -> None:
    """A -> B corrections deliberately don't re-dispatch: DexHandler's
    first-find gate is per (user, taxon), so cross-taxon re-dispatch
    would let one photo mint first_find / dex_count credit for
    arbitrarily many species."""
    _stub_token_verifier(monkeypatch)
    _wire_session(
        fake_session,
        user=_user_row(),
        obs=_obs_row(taxon_id=11111),
        species_cache_hit=_cached_species(),
    )
    dispatch_mock = AsyncMock(return_value=[])
    monkeypatch.setattr("app.api.routes.observations.dispatch", dispatch_mock)

    response = patch_client.patch(
        f"/v1/observations/{_OBS_ID}",
        json={"taxon_id": 12345},
        headers={"Authorization": "Bearer fake"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["taxon_id"] == 12345
    assert body["rewards"] == []
    dispatch_mock.assert_not_awaited()


def test_patch_redispatch_skipped_when_dex_already_minted(
    monkeypatch: pytest.MonkeyPatch,
    patch_client: TestClient,
    fake_session: AsyncMock,
) -> None:
    """One observation never mints two first finds: if a dex entry with
    first_observation_id == obs.id exists (pre-migration
    assigned-then-cleared residual), the re-dispatch is skipped entirely
    -- and critically, dispatched_at is NOT cleared, or the probe-less
    nightly replay would run the very dispatch the probe blocked."""
    _stub_token_verifier(monkeypatch)
    stamp = datetime(2026, 7, 1, 12, 0, 5, tzinfo=UTC)
    obs = _obs_row(dispatched_at=stamp)
    _wire_session(
        fake_session,
        user=_user_row(),
        obs=obs,
        species_cache_hit=_cached_species(),
        dispatch_minted_dex_id="01JDEXROW00000000000000ULID",
    )
    dispatch_mock = AsyncMock(return_value=[])
    monkeypatch.setattr("app.api.routes.observations.dispatch", dispatch_mock)

    response = patch_client.patch(
        f"/v1/observations/{_OBS_ID}",
        json={"taxon_id": 12345},
        headers={"Authorization": "Bearer fake"},
    )
    assert response.status_code == 200
    assert response.json()["rewards"] == []
    dispatch_mock.assert_not_awaited()
    # Only the field-patch commit -- no dispatched_at stamp.
    fake_session.commit.assert_awaited_once()
    # The stamp survives (replay stays skipped) while the marker closes
    # any future repick.
    assert obs.dispatched_at == stamp
    assert obs.taxon_first_assigned_at is not None


def test_patch_world_failure_leaves_dispatched_at_null_for_replay(
    monkeypatch: pytest.MonkeyPatch,
    patch_client: TestClient,
    fake_session: AsyncMock,
) -> None:
    """The taxon-time dispatch is the ONLY chance the Sanctuary
    contribution gets (WorldHandler skips taxonless creates) and
    WorldHandler swallows its own errors -- so when it reports failure,
    dispatched_at must stay NULL for the replay job to retry."""
    _stub_token_verifier(monkeypatch)
    obs = _obs_row()
    _wire_session(
        fake_session,
        user=_user_row(),
        obs=obs,
        species_cache_hit=_cached_species(),
        dispatch_photo=_photo_row(),
        dispatch_group=_group_row(),
    )

    async def fake_dispatch(ctx: Any, handlers: Any) -> list[Reward]:
        ctx.results["world"] = HandlerResult(rewards=[], state={"error": True})
        return []

    dispatch_mock = AsyncMock(side_effect=fake_dispatch)
    monkeypatch.setattr("app.api.routes.observations.dispatch", dispatch_mock)

    response = patch_client.patch(
        f"/v1/observations/{_OBS_ID}",
        json={"taxon_id": 12345},
        headers={"Authorization": "Bearer fake"},
    )
    assert response.status_code == 200
    dispatch_mock.assert_awaited_once()
    # No re-stamp: the replay's `dispatched_at IS NULL` filter keeps the
    # observation eligible until a healthy dispatch lands.
    assert obs.dispatched_at is None
    assert obs.taxon_first_assigned_at is not None
    assert fake_session.commit.await_count == 2


def test_patch_taxon_null_does_not_dispatch(
    monkeypatch: pytest.MonkeyPatch,
    patch_client: TestClient,
    fake_session: AsyncMock,
) -> None:
    """Clearing the taxon is not a transition -- nothing to re-dispatch."""
    _stub_token_verifier(monkeypatch)
    _wire_session(fake_session, user=_user_row(), obs=_obs_row(taxon_id=12345))
    dispatch_mock = AsyncMock(return_value=[])
    monkeypatch.setattr("app.api.routes.observations.dispatch", dispatch_mock)

    response = patch_client.patch(
        f"/v1/observations/{_OBS_ID}",
        json={"taxon_id": None},
        headers={"Authorization": "Bearer fake"},
    )
    assert response.status_code == 200
    assert response.json()["rewards"] == []
    dispatch_mock.assert_not_awaited()


def test_patch_place_name_only_does_not_dispatch(
    monkeypatch: pytest.MonkeyPatch,
    patch_client: TestClient,
    fake_session: AsyncMock,
) -> None:
    _stub_token_verifier(monkeypatch)
    _wire_session(fake_session, user=_user_row(), obs=_obs_row())
    dispatch_mock = AsyncMock(return_value=[])
    monkeypatch.setattr("app.api.routes.observations.dispatch", dispatch_mock)

    response = patch_client.patch(
        f"/v1/observations/{_OBS_ID}",
        json={"place_name": "Cincinnati, OH"},
        headers={"Authorization": "Bearer fake"},
    )
    assert response.status_code == 200
    assert response.json()["rewards"] == []
    dispatch_mock.assert_not_awaited()


def test_patch_dispatch_failure_still_returns_200(
    monkeypatch: pytest.MonkeyPatch,
    patch_client: TestClient,
    fake_session: AsyncMock,
) -> None:
    """Dispatch problems never fail the PATCH -- same contract as create.
    The kid keeps their species pick, just without the celebration. The
    route resets dispatched_at to NULL (the create-time dispatch already
    stamped it) so the nightly replay re-runs the full dispatch."""
    _stub_token_verifier(monkeypatch)
    obs = _obs_row()
    _wire_session(
        fake_session,
        user=_user_row(),
        obs=obs,
        species_cache_hit=_cached_species(),
        dispatch_photo=_photo_row(),
        dispatch_group=_group_row(),
    )
    dispatch_mock = AsyncMock(side_effect=RuntimeError("intentional"))
    monkeypatch.setattr("app.api.routes.observations.dispatch", dispatch_mock)

    response = patch_client.patch(
        f"/v1/observations/{_OBS_ID}",
        json={"taxon_id": 12345},
        headers={"Authorization": "Bearer fake"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["taxon_id"] == 12345
    assert body["rewards"] == []
    # The failure path rolls back the dead transaction, then issues a
    # direct UPDATE clearing dispatched_at so the replay's
    # `dispatched_at IS NULL` filter picks the row back up.
    fake_session.rollback.assert_awaited_once()
    reset_stmt = fake_session.execute.await_args_list[-1].args[0]
    assert isinstance(reset_stmt, Update)
    assert "dispatched_at" in str(reset_stmt)
    # Two commits: the field patch, then the dispatched_at reset.
    assert fake_session.commit.await_count == 2
    # The ORM instance itself was never stamped.
    assert obs.dispatched_at is None
