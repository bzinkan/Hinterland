"""Fail-closed parental-consent policy helpers.

The pre-signup consent receipt is not enough on its own.  Before family
setup can continue, the receipt must name the current policy version and be
linked to the canonical adult ``users.id``.  Keeping these queries in one
module prevents signup, group creation, and kid provisioning from drifting to
different definitions of "current consent".
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import models

CURRENT_PARENT_CONSENT_POLICY_VERSION = "2026-07-11-W1-INTERNAL"
CURRENT_PARENT_CONSENT_REQUIRED_MESSAGE = (
    "Current parental consent is required before family setup."
)


class CurrentParentConsentRequiredError(RuntimeError):
    """Raised when no receipt for the exact active policy can authorize setup."""


@dataclass(frozen=True)
class CurrentParentConsent:
    """The exact-policy receipt selected for an adult account."""

    record: models.ParentConsentRecord
    newly_linked: bool


def hash_browser_consent_nonce(consent_nonce: str) -> str:
    """Return the storage digest for a browser-held consent proof.

    The raw nonce is deliberately never persisted or logged. API validation
    constrains it to the 64-character lowercase-hex encoding of 32 random
    browser bytes.
    """
    return hashlib.sha256(consent_nonce.encode("ascii")).hexdigest()


async def acquire_current_parent_consent(
    session: AsyncSession,
    *,
    parent_user_id: str,
    verified_email: str | None,
    consent_id: str,
    consent_nonce: str,
) -> CurrentParentConsent:
    """Verify and atomically claim one exact browser-bound receipt.

    Email is an identity cross-check, never a receipt lookup key on its own.
    The caller must present the exact receipt ID and the high-entropy nonce
    held by the browser that recorded consent. ``FOR UPDATE`` serializes two
    attempts to claim the same receipt. A receipt linked to this same parent
    remains idempotent only when the exact proof still matches; a receipt
    linked to any other parent fails closed.

    The helper deliberately does not commit.  Signup can therefore persist a
    new parent and the receipt link in one transaction.
    """
    normalized_email = (verified_email or "").strip().lower()
    if not normalized_email:
        raise CurrentParentConsentRequiredError(CURRENT_PARENT_CONSENT_REQUIRED_MESSAGE)

    result = await session.execute(
        select(models.ParentConsentRecord)
        .where(
            models.ParentConsentRecord.id == consent_id,
            models.ParentConsentRecord.policy_version == CURRENT_PARENT_CONSENT_POLICY_VERSION,
            func.lower(models.ParentConsentRecord.parent_email) == normalized_email,
        )
        .with_for_update()
    )
    record = result.scalar_one_or_none()
    if record is None or record.browser_nonce_sha256 is None:
        raise CurrentParentConsentRequiredError(CURRENT_PARENT_CONSENT_REQUIRED_MESSAGE)

    presented_hash = hash_browser_consent_nonce(consent_nonce)
    if not hmac.compare_digest(record.browser_nonce_sha256, presented_hash):
        raise CurrentParentConsentRequiredError(CURRENT_PARENT_CONSENT_REQUIRED_MESSAGE)

    if record.linked_parent_user_id is None:
        record.linked_parent_user_id = parent_user_id
        return CurrentParentConsent(record=record, newly_linked=True)
    if record.linked_parent_user_id == parent_user_id:
        return CurrentParentConsent(record=record, newly_linked=False)
    raise CurrentParentConsentRequiredError(CURRENT_PARENT_CONSENT_REQUIRED_MESSAGE)


async def require_linked_current_parent_consent(
    session: AsyncSession,
    *,
    parent_user_id: str,
) -> models.ParentConsentRecord:
    """Require a receipt already linked to the adult and active policy."""
    result = await session.execute(
        select(models.ParentConsentRecord)
        .where(
            models.ParentConsentRecord.linked_parent_user_id == parent_user_id,
            models.ParentConsentRecord.policy_version == CURRENT_PARENT_CONSENT_POLICY_VERSION,
            models.ParentConsentRecord.browser_nonce_sha256.is_not(None),
        )
        .order_by(models.ParentConsentRecord.recorded_at.desc())
        .limit(1)
    )
    record = result.scalar_one_or_none()
    if record is None:
        raise CurrentParentConsentRequiredError(CURRENT_PARENT_CONSENT_REQUIRED_MESSAGE)
    return record
