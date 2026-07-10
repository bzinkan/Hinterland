"""Shared test stubs for token verification across the Hinterland auth rewrite.

Phase 6a replaces the single Firebase verifier
(`app.core.auth.verify_firebase_id_token`) with a two-path bearer-token
entry point (`app.core.auth.verify_token`) that handles both Entra ID
adult tokens and Hinterland RS256 kid session tokens.

This module exposes a single ``stub_token_verifier`` helper that patches
whichever entry point the current source tree exposes -- so the same
test files keep working before and after the auth rewrite lands.

Default keyword arguments preserve the legacy flavor (uid + email) so
existing call sites can drop in the import with no signature change.
"""

from __future__ import annotations

from typing import Any

import pytest

import app.core.auth as auth_module
from app.core.config import Settings


def stub_token_verifier(
    monkeypatch: pytest.MonkeyPatch,
    *,
    uid: str = "firebase-parent-001",
    role: str | None = None,
    group_id: str | None = None,
    email: str | None = "parent@example.com",
    parent_id: str | None = None,
    kid_id: str | None = None,
    teacher_id: str | None = None,
    entra_oid: str | None = None,
    raises: Exception | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    """Patch the bearer-token verifier to return synthesized claims.

    Builds a claims dict containing only the non-``None`` keys so callers
    can opt out of role/group_id permutations. The claims are deliberately
    free of the ``oid`` / ``token_type`` markers that the post-rewrite
    ``get_current_user`` looks for, which triggers its back-compat
    short-circuit and lets tests keep using their existing session mocks
    without wiring up a real DB lookup for every case.

    Patches whichever of these symbols exists on ``app.core.auth``:

    * ``verify_token`` -- the new two-path entry point.
    * ``verify_firebase_id_token`` -- the legacy single-path verifier.
    * ``verify_bearer_token`` -- alternative new-path name used by some
      plan variants. Returns ``(path, claims)`` tuple instead of a bare
      claims dict.

    Multiple symbols may be patched in a single call if they coexist
    during the rewrite window.
    """
    claims: dict[str, Any] = {"uid": uid}
    if email is not None:
        claims["email"] = email
    if role is not None:
        claims["role"] = role
    if group_id is not None:
        claims["group_id"] = group_id
    if parent_id is not None:
        claims["parent_id"] = parent_id
    if kid_id is not None:
        claims["kid_id"] = kid_id
    if teacher_id is not None:
        claims["teacher_id"] = teacher_id
    if entra_oid is not None:
        claims["oid"] = entra_oid
    if extra:
        claims.update(extra)

    def fake_verify_claims(token: str, settings: Settings) -> dict[str, Any]:
        if raises is not None:
            raise raises
        return claims

    def fake_verify_bearer(token: str, settings: Settings) -> tuple[str, dict[str, Any]]:
        if raises is not None:
            raise raises
        path = "hinterland" if role == "kid" else "entra"
        return (path, claims)

    patched_any = False
    if hasattr(auth_module, "verify_token"):
        monkeypatch.setattr(auth_module, "verify_token", fake_verify_claims)
        patched_any = True
    if hasattr(auth_module, "verify_firebase_id_token"):
        monkeypatch.setattr(auth_module, "verify_firebase_id_token", fake_verify_claims)
        patched_any = True
    if hasattr(auth_module, "verify_bearer_token"):
        monkeypatch.setattr(auth_module, "verify_bearer_token", fake_verify_bearer)
        patched_any = True

    if not patched_any:  # pragma: no cover -- guards against future renames
        raise RuntimeError(
            "stub_token_verifier could not find a verifier symbol on app.core.auth. "
            "Expected one of: verify_token, verify_firebase_id_token, verify_bearer_token."
        )


# Convenience constants matching the values most test files use today.
DEFAULT_ENTRA_UID = "firebase-parent-001"
DEFAULT_KID_UID = "firebase-kid-xyz"
DEFAULT_GROUP_ID = "01J0GROUPIDABCDEFGHIJKLMNO"
