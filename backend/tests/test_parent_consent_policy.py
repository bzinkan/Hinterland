"""Browser-bound exact-policy tests for the parental-consent boundary."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.parent_consent import (
    CURRENT_PARENT_CONSENT_POLICY_VERSION,
    CurrentParentConsentRequiredError,
    acquire_current_parent_consent,
    hash_browser_consent_nonce,
    require_linked_current_parent_consent,
)
from app.db import models

_PARENT_ID = "01J0PARENTID0000000000ULID"
_OTHER_PARENT_ID = "01J0OTHERPARENT00000000000"
_CONSENT_ID = "01J0CONSENTID0000000000000"
_NONCE = "a" * 64


def _record(
    *,
    policy_version: str = CURRENT_PARENT_CONSENT_POLICY_VERSION,
    linked_parent_user_id: str | None = None,
    nonce_hash: str | None = hash_browser_consent_nonce(_NONCE),
) -> models.ParentConsentRecord:
    return models.ParentConsentRecord(
        id=_CONSENT_ID,
        parent_email="Parent@Example.com",
        policy_version=policy_version,
        source="web_consent",
        recorded_at=datetime.now(UTC),
        browser_nonce_sha256=nonce_hash,
        linked_parent_user_id=linked_parent_user_id,
    )


def _result(value: models.ParentConsentRecord | None) -> MagicMock:
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


def _statement_values(statement: object) -> set[object]:
    compiled = statement.compile()  # type: ignore[attr-defined]
    return set(compiled.params.values())


async def test_acquire_accepts_exact_proof_already_linked_to_same_parent() -> None:
    session = AsyncMock(spec=AsyncSession)
    linked = _record(linked_parent_user_id=_PARENT_ID)
    session.execute = AsyncMock(return_value=_result(linked))

    acquired = await acquire_current_parent_consent(
        session,
        parent_user_id=_PARENT_ID,
        verified_email="parent@example.com",
        consent_id=_CONSENT_ID,
        consent_nonce=_NONCE,
    )

    assert acquired.record is linked
    assert acquired.newly_linked is False
    statement = session.execute.await_args.args[0]
    values = _statement_values(statement)
    assert _CONSENT_ID in values
    assert CURRENT_PARENT_CONSENT_POLICY_VERSION in values
    assert "parent@example.com" in values
    assert "FOR UPDATE" in str(statement)


async def test_acquire_claims_only_the_exact_unlinked_receipt() -> None:
    session = AsyncMock(spec=AsyncSession)
    unlinked = _record()
    session.execute = AsyncMock(return_value=_result(unlinked))

    acquired = await acquire_current_parent_consent(
        session,
        parent_user_id=_PARENT_ID,
        verified_email="  PARENT@example.com ",
        consent_id=_CONSENT_ID,
        consent_nonce=_NONCE,
    )

    assert acquired.record is unlinked
    assert acquired.newly_linked is True
    assert unlinked.linked_parent_user_id == _PARENT_ID


@pytest.mark.parametrize("verified_email", [None, "", "   "])
async def test_acquire_fails_closed_without_verified_email(
    verified_email: str | None,
) -> None:
    session = AsyncMock(spec=AsyncSession)

    with pytest.raises(CurrentParentConsentRequiredError):
        await acquire_current_parent_consent(
            session,
            parent_user_id=_PARENT_ID,
            verified_email=verified_email,
            consent_id=_CONSENT_ID,
            consent_nonce=_NONCE,
        )

    session.execute.assert_not_awaited()


@pytest.mark.parametrize(
    ("record", "nonce"),
    [
        (None, _NONCE),
        (_record(nonce_hash=None), _NONCE),
        (_record(), "b" * 64),
        (_record(linked_parent_user_id=_OTHER_PARENT_ID), _NONCE),
    ],
)
async def test_acquire_rejects_missing_legacy_wrong_nonce_or_other_parent(
    record: models.ParentConsentRecord | None,
    nonce: str,
) -> None:
    session = AsyncMock(spec=AsyncSession)
    session.execute = AsyncMock(return_value=_result(record))

    with pytest.raises(CurrentParentConsentRequiredError):
        await acquire_current_parent_consent(
            session,
            parent_user_id=_PARENT_ID,
            verified_email="parent@example.com",
            consent_id=_CONSENT_ID,
            consent_nonce=nonce,
        )


async def test_require_linked_current_consent_filters_out_legacy_null_nonce_hash() -> None:
    session = AsyncMock(spec=AsyncSession)
    session.execute = AsyncMock(return_value=_result(None))

    with pytest.raises(CurrentParentConsentRequiredError):
        await require_linked_current_parent_consent(
            session,
            parent_user_id=_PARENT_ID,
        )

    statement = session.execute.await_args.args[0]
    assert CURRENT_PARENT_CONSENT_POLICY_VERSION in _statement_values(statement)
    assert "browser_nonce_sha256 IS NOT NULL" in str(statement)
