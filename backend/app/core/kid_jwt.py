"""Hinterland RS256 kid JWT mint + verify + JWKS publication (Phase 6a).

The kid auth flow uses two short-lived RS256 JWTs minted by this backend:

* ``handoff`` -- 15-minute single-use token returned to the parent after
  ``POST /v1/groups/{group_id}/kids``. The kid's device exchanges it via
  ``POST /v1/auth/kid-exchange`` for a longer-lived session JWT. Single-use
  is enforced atomically by an INSERT into ``kid_handoff_jti``.
* ``session`` -- 30-day token the kid app sends as the bearer credential on
  every subsequent request. Verified by :func:`verify_dragonfly_jwt`.

Both tokens use the same kid header (``settings.dragonfly_jwt_kid``,
``k1-2026-06`` at the time of writing) and the same RSA key pair, which lives
in Azure Key Vault and is loaded by :mod:`app.core.key_vault`.

The public JWKS document returned from
``GET /.well-known/dragonfly-kid-jwks.json`` is produced by
:func:`public_jwks` -- one entry, the active kid. Future key rotation will
publish the previous + current kids side-by-side during the overlap window.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import TYPE_CHECKING, Any, Literal, TypedDict, cast

import jwt
import structlog
from jwt.exceptions import InvalidTokenError
from ulid import ULID

from app.core.key_vault import get_kid_public_pem, get_kid_signing_pem

if TYPE_CHECKING:  # pragma: no cover
    from app.core.config import Settings

log = structlog.get_logger()


class InvalidDragonflyJwt(Exception):
    """Raised when a Hinterland RS256 JWT fails signature, claim, or type checks."""


class DragonflyTokenClaims(TypedDict, total=False):
    """Decoded shape of a Hinterland handoff/session JWT."""

    sub: str
    iss: str
    aud: str
    iat: int
    exp: int
    jti: str
    role: str
    group_id: str
    parent_id: str
    token_type: Literal["handoff", "session"]


# Claims required on every verified token. ``jti`` is required on handoffs
# (single-use ledger) but optional on session tokens; we enforce it at
# verify time only for the handoff path.
_REQUIRED_CLAIMS_COMMON = ("exp", "iat", "iss", "aud", "sub", "token_type")


def _now() -> datetime:
    """Wrapped so tests can monkeypatch a deterministic clock."""
    return datetime.now(UTC)


def _build_payload(
    *,
    kid_user_id: str,
    parent_id: str,
    group_id: str,
    settings: Settings,
    token_type: Literal["handoff", "session"],
    ttl_seconds: int,
    jti: str | None,
) -> dict[str, Any]:
    issued_at = _now()
    payload: dict[str, Any] = {
        "iss": settings.dragonfly_jwt_issuer,
        "aud": settings.dragonfly_jwt_audience,
        "sub": kid_user_id,
        "iat": int(issued_at.timestamp()),
        "exp": int((issued_at + timedelta(seconds=ttl_seconds)).timestamp()),
        "role": "kid",
        "group_id": group_id,
        "parent_id": parent_id,
        "token_type": token_type,
    }
    if jti is not None:
        payload["jti"] = jti
    return payload


def _encode(payload: dict[str, Any], *, settings: Settings) -> str:
    headers = {
        "kid": settings.dragonfly_jwt_kid,
        "alg": "RS256",
        "typ": "JWT",
    }
    signing_pem = get_kid_signing_pem(settings)
    token = jwt.encode(payload, signing_pem, algorithm="RS256", headers=headers)
    # PyJWT 2.x returns ``str``; older 1.x returned ``bytes``. Normalize.
    if isinstance(token, bytes):
        return token.decode("utf-8")
    return token


def mint_handoff_token(
    *,
    kid_user_id: str,
    parent_id: str,
    group_id: str,
    settings: Settings,
) -> tuple[str, str]:
    """Mint a single-use handoff JWT. Returns ``(token, jti)``.

    The caller MUST treat the returned ``jti`` as the canonical identity
    for replay protection -- the kid-exchange handler INSERTs the jti into
    ``kid_handoff_jti`` and a duplicate-PK violation IS the single-use
    enforcement.
    """
    jti = str(ULID())
    payload = _build_payload(
        kid_user_id=kid_user_id,
        parent_id=parent_id,
        group_id=group_id,
        settings=settings,
        token_type="handoff",
        ttl_seconds=settings.dragonfly_handoff_ttl_seconds,
        jti=jti,
    )
    token = _encode(payload, settings=settings)
    log.info(
        "kid_jwt.handoff_minted",
        kid_user_id=kid_user_id,
        jti=jti,
        ttl_seconds=settings.dragonfly_handoff_ttl_seconds,
    )
    return token, jti


def mint_session_token(
    *,
    kid_user_id: str,
    parent_id: str,
    group_id: str,
    settings: Settings,
) -> str:
    """Mint a long-lived (30-day default) session JWT for a kid.

    Unlike handoff tokens, session tokens still embed a fresh ``jti`` so
    they can be revoked individually in a future denylist sweep, but the
    backend does not (currently) require single-use semantics.
    """
    jti = str(ULID())
    payload = _build_payload(
        kid_user_id=kid_user_id,
        parent_id=parent_id,
        group_id=group_id,
        settings=settings,
        token_type="session",
        ttl_seconds=settings.dragonfly_session_ttl_seconds,
        jti=jti,
    )
    token = _encode(payload, settings=settings)
    log.info(
        "kid_jwt.session_minted",
        kid_user_id=kid_user_id,
        jti=jti,
        ttl_seconds=settings.dragonfly_session_ttl_seconds,
    )
    return token


def verify_dragonfly_jwt(
    token: str,
    *,
    settings: Settings,
    expected_token_type: Literal["handoff", "session"] | None = None,
) -> DragonflyTokenClaims:
    """Verify a Hinterland RS256 JWT and return its decoded claims.

    Wraps every PyJWT failure mode (bad signature, expiry, audience,
    issuer, missing required claims) as :class:`InvalidDragonflyJwt` so
    callers can surface a single 401 response.

    When ``expected_token_type`` is supplied, the ``token_type`` claim
    must match exactly -- a session JWT cannot be exchanged via the
    handoff endpoint, and vice versa.
    """
    public_pem = get_kid_public_pem(settings)
    required_claims = list(_REQUIRED_CLAIMS_COMMON)
    if expected_token_type == "handoff":
        required_claims.append("jti")

    try:
        decoded = jwt.decode(
            token,
            public_pem,
            algorithms=["RS256"],
            audience=settings.dragonfly_jwt_audience,
            issuer=settings.dragonfly_jwt_issuer,
            options={
                "require": required_claims,
                "verify_signature": True,
                "verify_exp": True,
                "verify_iat": True,
                "verify_aud": True,
                "verify_iss": True,
            },
        )
    except InvalidTokenError as exc:
        raise InvalidDragonflyJwt(str(exc)) from exc

    token_type = decoded.get("token_type")
    if expected_token_type is not None and token_type != expected_token_type:
        raise InvalidDragonflyJwt(
            f"Expected token_type={expected_token_type!r}, got {token_type!r}"
        )

    return cast(DragonflyTokenClaims, decoded)


def _b64url_uint(value: int) -> str:
    """Encode a non-negative integer as base64url without padding (RFC 7518)."""
    byte_length = (value.bit_length() + 7) // 8 or 1
    raw = value.to_bytes(byte_length, "big")
    from base64 import urlsafe_b64encode

    return urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


@lru_cache(maxsize=4)
def _public_jwks_for_kid(kid: str, public_pem: bytes) -> dict[str, object]:
    """Build a JWKS dict for the given (kid, PEM) pair.

    Cached so we don't re-derive the RSA public numbers on every JWKS GET.
    """
    from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey
    from cryptography.hazmat.primitives.serialization import load_pem_public_key

    key = load_pem_public_key(public_pem)
    if not isinstance(key, RSAPublicKey):
        raise InvalidDragonflyJwt("kid-jwt public key is not RSA")
    numbers = key.public_numbers()
    return {
        "keys": [
            {
                "kty": "RSA",
                "use": "sig",
                "alg": "RS256",
                "kid": kid,
                "n": _b64url_uint(numbers.n),
                "e": _b64url_uint(numbers.e),
            }
        ]
    }


def public_jwks(settings: Settings) -> dict[str, object]:
    """Return the Hinterland kid-JWT JWKS document.

    Served at ``/.well-known/dragonfly-kid-jwks.json``. Cached for the
    process lifetime since the kid + key rotate rarely (manual operator
    action).
    """
    public_pem = get_kid_public_pem(settings)
    return _public_jwks_for_kid(settings.dragonfly_jwt_kid, public_pem)


def clear_caches() -> None:
    """Drop the JWKS cache. Intended for tests only."""
    _public_jwks_for_kid.cache_clear()
