"""Canonical-parent authority for identification correction and deletion."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException, status
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.parent_consent import CurrentParentConsentRequiredError
from app.db import models
from app.db.session import get_db_session
from app.main import create_app
from tests.helpers.auth import stub_token_verifier

PARENT_A_ID = "01J0PARENTA0000000000000UL"
PARENT_B_ID = "01J0PARENTB0000000000000UL"
CHILD_A_ID = "01J0CHILDA00000000000000UL"
GROUP_ID = "01J0GROUPA00000000000000UL"
OBSERVATION_ID = "01J0OBSERVATION000000000UL"
PHOTO_ID = "01J0PHOTO000000000000000UL"
REVIEW_ID = "01J0REVIEW00000000000000UL"


def _result(value: object) -> MagicMock:
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=value)
    return result


def _lifecycle_result(observation: models.Observation) -> MagicMock:
    photo = models.Photo(
        id=observation.photo_id,
        user_id=observation.user_id,
        bucket="test",
        object_name=f"pending/finalized/{observation.photo_id}.jpg",
        status="pending",
        attachment_status="attached",
    )
    result = MagicMock()
    result.one_or_none = MagicMock(return_value=(photo, None))
    return result


def _parent(parent_id: str = PARENT_A_ID) -> models.User:
    return models.User(
        id=parent_id,
        entra_oid=f"entra-{parent_id}",
        role="parent",
        display_name="Parent",
    )


def _child() -> models.User:
    return models.User(
        id=CHILD_A_ID,
        role="kid",
        display_name="Child A",
        parent_user_id=PARENT_A_ID,
    )


def _observation(*, moderation_status: str = "pilot_private") -> models.Observation:
    return models.Observation(
        id=OBSERVATION_ID,
        user_id=CHILD_A_ID,
        group_id=GROUP_ID,
        photo_id=PHOTO_ID,
        submission_key="01J0SUBMISSION00000000000U",
        observed_at=datetime.now(UTC),
        location_source="none",
        identification_source="unknown",
        identification_revision=1,
        dispatch_status="complete",
        moderation_status=moderation_status,
        moderation_source="noop",
        rewards=[],
        ecology_tags={},
    )


def _review(*, review_status: str) -> models.ReviewQueueItem:
    return models.ReviewQueueItem(
        id=REVIEW_ID,
        group_id=GROUP_ID,
        photo_id=PHOTO_ID,
        observation_id=OBSERVATION_ID,
        status=review_status,
        reason="moderation",
    )


@pytest.fixture
def fake_session() -> AsyncMock:
    session = AsyncMock(spec=AsyncSession)
    session.add = MagicMock()
    session.flush = AsyncMock()
    session.commit = AsyncMock()
    session.refresh = AsyncMock()
    return session


@pytest.fixture
def fake_storage() -> MagicMock:
    return MagicMock()


@pytest.fixture
def client(fake_session: AsyncMock, fake_storage: MagicMock) -> Iterator[TestClient]:
    app = create_app(Settings(env="local", app_version="test"))
    app.state.signed_url_generator = fake_storage

    async def override() -> AsyncIterator[AsyncSession]:
        yield fake_session

    app.dependency_overrides[get_db_session] = override
    with TestClient(app, raise_server_exceptions=True) as test_client:
        yield test_client


def _authenticate(monkeypatch: pytest.MonkeyPatch, user: models.User) -> None:
    stub_token_verifier(
        monkeypatch,
        uid=user.id,
        role=user.role,
        group_id=GROUP_ID,
        email="parent@example.com" if user.role == "parent" else None,
    )

    async def resolve(
        session: AsyncSession,
        current_user: object,
        *,
        allowed_roles: set[str] | frozenset[str] | None = None,
        missing_user_status: int = status.HTTP_403_FORBIDDEN,
    ) -> models.User:
        del session, current_user, missing_user_status
        if allowed_roles is not None and user.role not in allowed_roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Role denied")
        return user

    monkeypatch.setattr("app.api.routes.observations.resolve_current_user_row", resolve)


def _allow_current_consent(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    consent = AsyncMock(return_value=MagicMock())
    monkeypatch.setattr(
        "app.api.routes.observations.require_linked_current_parent_consent",
        consent,
    )
    return consent


def _rebuild() -> models.DerivedStateRebuild:
    return models.DerivedStateRebuild(
        id="01J0REBUILD0000000000000UL",
        user_id=CHILD_A_ID,
        trigger_observation_id=OBSERVATION_ID,
        status="queued",
        attempt_count=0,
    )


def test_parent_can_correct_only_own_child_and_rebuild_is_child_scoped(
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
    fake_session: AsyncMock,
) -> None:
    parent = _parent()
    _authenticate(monkeypatch, parent)
    consent = _allow_current_consent(monkeypatch)
    observation = _observation()
    fake_session.execute = AsyncMock(
        side_effect=[
            _result(CHILD_A_ID),
            MagicMock(),  # child advisory lock
            _result(observation),
            _lifecycle_result(observation),
        ]
    )
    enqueue = AsyncMock(return_value=_rebuild())
    monkeypatch.setattr("app.api.routes.observations.enqueue_rebuild", enqueue)

    response = client.post(
        f"/v1/observations/{OBSERVATION_ID}/identification",
        json={"source": "unknown", "expected_revision": 1},
        headers={"Authorization": "Bearer parent-a"},
    )

    assert response.status_code == 200
    assert response.json()["observation"]["identification_revision"] == 2
    consent.assert_awaited_once_with(fake_session, parent_user_id=PARENT_A_ID)
    enqueue.assert_awaited_once_with(
        fake_session,
        user_id=CHILD_A_ID,
        trigger_observation_id=OBSERVATION_ID,
    )
    statements = [str(call.args[0]) for call in fake_session.execute.await_args_list]
    assert "parent_user_id" in statements[0]
    assert "pg_advisory_xact_lock" in statements[1]
    assert "parent_user_id" in statements[2]


@pytest.mark.parametrize("endpoint", ["identification", "delete"])
def test_other_parent_or_group_owner_gets_indistinguishable_404(
    endpoint: str,
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
    fake_session: AsyncMock,
) -> None:
    _authenticate(monkeypatch, _parent(PARENT_B_ID))
    _allow_current_consent(monkeypatch)
    fake_session.execute = AsyncMock(side_effect=[_result(None)])
    revoke = AsyncMock()
    monkeypatch.setattr(
        "app.api.routes.observations.revoke_and_reject_review_item",
        revoke,
    )

    if endpoint == "identification":
        response = client.post(
            f"/v1/observations/{OBSERVATION_ID}/identification",
            json={"source": "unknown", "expected_revision": 1},
            headers={"Authorization": "Bearer parent-b"},
        )
    else:
        response = client.delete(
            f"/v1/observations/{OBSERVATION_ID}",
            headers={"Authorization": "Bearer parent-b"},
        )

    assert response.status_code == 404
    assert response.json()["error"]["message"] == "Observation not found"
    revoke.assert_not_awaited()


@pytest.mark.parametrize("endpoint", ["identification", "delete"])
def test_parent_mutation_requires_current_linked_consent(
    endpoint: str,
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
    fake_session: AsyncMock,
) -> None:
    _authenticate(monkeypatch, _parent())
    consent = AsyncMock(side_effect=CurrentParentConsentRequiredError("required"))
    monkeypatch.setattr(
        "app.api.routes.observations.require_linked_current_parent_consent",
        consent,
    )

    if endpoint == "identification":
        response = client.post(
            f"/v1/observations/{OBSERVATION_ID}/identification",
            json={"source": "unknown", "expected_revision": 1},
            headers={"Authorization": "Bearer parent-a"},
        )
    else:
        response = client.delete(
            f"/v1/observations/{OBSERVATION_ID}",
            headers={"Authorization": "Bearer parent-a"},
        )

    assert response.status_code == 409
    assert "Current parental consent" in response.json()["error"]["message"]
    fake_session.execute.assert_not_awaited()


@pytest.mark.parametrize("review_status", ["pending", "approved"])
def test_parent_delete_uses_durable_review_revocation_service(
    review_status: str,
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
    fake_session: AsyncMock,
    fake_storage: MagicMock,
) -> None:
    parent = _parent()
    _authenticate(monkeypatch, parent)
    _allow_current_consent(monkeypatch)
    observation = _observation(
        moderation_status="clean" if review_status == "approved" else "pending"
    )
    review = _review(review_status=review_status)
    fake_session.execute = AsyncMock(
        side_effect=[
            _result(CHILD_A_ID),
            MagicMock(),  # child advisory lock
            _result(observation),
            _result(review),
        ]
    )
    revoke = AsyncMock(return_value=_rebuild())
    monkeypatch.setattr(
        "app.api.routes.observations.revoke_and_reject_review_item",
        revoke,
    )

    response = client.delete(
        f"/v1/observations/{OBSERVATION_ID}",
        headers={"Authorization": "Bearer parent-a"},
    )

    assert response.status_code == 204
    revoke.assert_awaited_once_with(
        fake_session,
        storage=fake_storage,
        review=review,
        reviewer_user_id=PARENT_A_ID,
        source="parent_delete",
        claim_review_status=review_status,
    )


def test_parent_delete_synthesizes_parent_delete_review_when_none_exists(
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
    fake_session: AsyncMock,
) -> None:
    _authenticate(monkeypatch, _parent())
    _allow_current_consent(monkeypatch)
    observation = _observation()
    fake_session.execute = AsyncMock(
        side_effect=[
            _result(CHILD_A_ID),
            MagicMock(),
            _result(observation),
            _result(None),
        ]
    )
    revoke = AsyncMock(return_value=_rebuild())
    monkeypatch.setattr(
        "app.api.routes.observations.revoke_and_reject_review_item",
        revoke,
    )

    response = client.delete(
        f"/v1/observations/{OBSERVATION_ID}",
        headers={"Authorization": "Bearer parent-a"},
    )

    assert response.status_code == 204
    synthetic = fake_session.add.call_args.args[0]
    assert isinstance(synthetic, models.ReviewQueueItem)
    assert synthetic.reason == "parent_delete"
    assert synthetic.status == "pending"
    assert synthetic.observation_id == OBSERVATION_ID
    assert synthetic.photo_id == PHOTO_ID
    fake_session.flush.assert_awaited_once()
    assert revoke.await_args is not None
    assert revoke.await_args.kwargs["review"] is synthetic


def test_parent_delete_links_legacy_photo_only_review_before_revocation(
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
    fake_session: AsyncMock,
) -> None:
    _authenticate(monkeypatch, _parent())
    _allow_current_consent(monkeypatch)
    observation = _observation()
    review = _review(review_status="pending")
    review.observation_id = None
    fake_session.execute = AsyncMock(
        side_effect=[
            _result(CHILD_A_ID),
            MagicMock(),
            _result(observation),
            _result(review),
        ]
    )
    revoke = AsyncMock(return_value=_rebuild())
    monkeypatch.setattr(
        "app.api.routes.observations.revoke_and_reject_review_item",
        revoke,
    )

    response = client.delete(
        f"/v1/observations/{OBSERVATION_ID}",
        headers={"Authorization": "Bearer parent-a"},
    )

    assert response.status_code == 204
    assert review.observation_id == OBSERVATION_ID
    fake_session.flush.assert_awaited_once()
    assert revoke.await_args is not None
    assert revoke.await_args.kwargs["review"] is review


def test_parent_delete_is_idempotent_for_own_rejected_observation(
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
    fake_session: AsyncMock,
) -> None:
    _authenticate(monkeypatch, _parent())
    _allow_current_consent(monkeypatch)
    observation = _observation(moderation_status="rejected")
    observation.rejected_at = datetime.now(UTC)
    fake_session.execute = AsyncMock(
        side_effect=[_result(CHILD_A_ID), MagicMock(), _result(observation)]
    )
    revoke = AsyncMock()
    monkeypatch.setattr(
        "app.api.routes.observations.revoke_and_reject_review_item",
        revoke,
    )

    response = client.delete(
        f"/v1/observations/{OBSERVATION_ID}",
        headers={"Authorization": "Bearer parent-a"},
    )

    assert response.status_code == 204
    revoke.assert_not_awaited()


def test_kid_cannot_delete_observation(
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
    fake_session: AsyncMock,
) -> None:
    _authenticate(monkeypatch, _child())
    revoke = AsyncMock()
    monkeypatch.setattr(
        "app.api.routes.observations.revoke_and_reject_review_item",
        revoke,
    )

    response = client.delete(
        f"/v1/observations/{OBSERVATION_ID}",
        headers={"Authorization": "Bearer kid"},
    )

    assert response.status_code == 403
    revoke.assert_not_awaited()
    fake_session.execute.assert_not_awaited()


def test_parent_delete_conflicts_after_public_submission(
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
    fake_session: AsyncMock,
) -> None:
    _authenticate(monkeypatch, _parent())
    _allow_current_consent(monkeypatch)
    observation = _observation(moderation_status="clean")
    observation.inat_observation_id = 12345
    observation.submitted_to_inat_at = datetime.now(UTC)
    fake_session.execute = AsyncMock(
        side_effect=[_result(CHILD_A_ID), MagicMock(), _result(observation)]
    )
    revoke = AsyncMock()
    monkeypatch.setattr(
        "app.api.routes.observations.revoke_and_reject_review_item",
        revoke,
    )

    response = client.delete(
        f"/v1/observations/{OBSERVATION_ID}",
        headers={"Authorization": "Bearer parent-a"},
    )

    assert response.status_code == 409
    assert "publicly submitted" in response.json()["error"]["message"]
    revoke.assert_not_awaited()
