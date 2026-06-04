"""Google OIDC auth for the `/internal/*` routes -- TRANSITIONAL.

ADR 0010 moves Dragonfly's runtime to Azure. The production async
delivery path is now Event Grid -> Service Bus -> Container Apps
workers calling service functions directly under managed identity;
the `/internal/*` HTTP routes (see `internal_moderation.py` and
`internal_inat.py`) are demoted to **manual / admin retries only**
and are no longer on the production trust boundary.

This module remains the auth dependency on those routes for now.
A follow-up replaces it with an Azure HMAC signature backed by a
Key Vault secret -- simpler than JWKS, no Google dependency, and
appropriately scoped to the small surface area the routes still
carry. Until that lands, the Google-OIDC verification path stays
functional so existing operator tooling keeps working.

Local dev opts out so the smoke scripts + moderation processor unit
tests don't need a Google identity. The opt-out is the `env == "local"`
default; explicit `DRAGONFLY_INTERNAL_OIDC_REQUIRED=true|false` wins
in either direction. See `Settings.require_internal_oidc` in
`app/core/config.py` for the resolution rule.

The actual signature verification is delegated to
`verify_google_oidc_token`, factored out as a module-level function so
tests can `monkeypatch.setattr(internal_auth_module,
"verify_google_oidc_token", fake)` without touching the dependency
graph.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request, status

from app.core.config import Settings, get_request_settings


class InternalAuthError(Exception):
    """Raised by `verify_google_oidc_token` on bad / unverifiable tokens.

    The dependency catches this and maps it to a 401; the indirection
    keeps the dependency body free of `google.auth.exceptions` imports
    so tests don't have to pull the SDK in.
    """


class InternalAuthMisconfigured(Exception):
    """Raised when the verifier cannot run at all (e.g. google-auth not installed).

    Mapped to 503 (the same bucket as missing audience / allowlist), not 401 --
    the right operator alert is "rebuild the image" / "check deploy", not
    "Eventarc service account got rotated."
    """


@dataclass(frozen=True)
class InternalPrincipal:
    """The verified caller identity behind a `/internal/*` request."""

    email: str
    audience: str
    claims: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Verifier seam -- tests monkeypatch this; production goes to google-auth.
# ---------------------------------------------------------------------------


# Defense-in-depth: explicit issuer pin. google-auth's
# verify_oauth2_token already enforces this against the same set, but
# pinning it in-module protects against a future google-auth release
# broadening the accepted issuer set (or an upstream bug).
_GOOGLE_OIDC_ISSUERS = frozenset(
    {
        "accounts.google.com",
        "https://accounts.google.com",
    }
)


def verify_google_oidc_token(token: str, audience: str) -> dict[str, Any]:
    """Verify a Google-issued OIDC ID token; return the decoded claims.

    Raises `InternalAuthError` for any verification failure (bad
    signature, expired, audience mismatch, key fetch outage, claims
    missing the expected issuer, etc.). The dependency turns that into
    a 401 with no leakage of the underlying reason.

    Raises `InternalAuthMisconfigured` ONLY when the verifier library
    itself is unavailable (e.g. supply-chain regression dropped
    google-auth from the image). The dependency maps that to 503 so
    the operator alert points at the deploy, not at the caller.
    """
    # Lazy import keeps the dependency optional at import time -- the
    # local dev path imports `internal_auth` without google-auth
    # installed. `google-auth` is a small (~1MB) JWT-verifying library
    # that does not pull in the broader google-cloud / firebase-admin
    # surface that Phase 11b removed.
    try:
        from google.auth.transport.requests import Request as GoogleRequest
        from google.oauth2 import id_token
    except ImportError as exc:  # pragma: no cover - install-time issue
        raise InternalAuthMisconfigured(
            "google-auth is not installed; cannot verify OIDC tokens",
        ) from exc

    try:
        # google-auth has no py.typed marker so mypy can't resolve the
        # return signature; the project's existing pattern is to silence
        # the `no-untyped-call` on the boundary call and then narrow the
        # result via the `isinstance(claims, dict)` check below.
        claims = id_token.verify_oauth2_token(  # type: ignore[no-untyped-call]
            token,
            GoogleRequest(),
            audience=audience,
        )
    except Exception as exc:
        # google-auth raises ValueError for audience/signature/expiry
        # and GoogleAuthError for JWKS transport failures. Catching
        # broadly here means an unexpected exception type (KeyError on
        # malformed claims, asyncio.CancelledError, etc.) still maps
        # to the documented 401 contract instead of leaking as 500.
        # InternalAuthMisconfigured deliberately escapes -- it's a
        # different bucket (503) and we don't want to swallow it here.
        if isinstance(exc, InternalAuthMisconfigured):
            raise
        raise InternalAuthError(str(exc)) from exc

    if not isinstance(claims, dict):  # pragma: no cover - defensive
        raise InternalAuthError("OIDC token decoded to a non-dict")

    # Defense-in-depth: explicit issuer pin + email_verified gate. Both
    # are practically enforced upstream today (google-auth pins issuer;
    # the .iam.gserviceaccount.com allowlist blocks unverified emails
    # because Google does not issue email_verified=true tokens for SAs
    # at non-google domains), but in-module enforcement protects
    # against a future upstream change.
    iss = claims.get("iss")
    if iss not in _GOOGLE_OIDC_ISSUERS:
        raise InternalAuthError(f"unexpected issuer: {iss!r}")

    # For Google service accounts the `email_verified` claim is always
    # `True`; for end-user accounts it can be False on unverified
    # mailboxes. Either way: only verified emails are usable identities
    # against the allowlist.
    if claims.get("email_verified") is not True:
        raise InternalAuthError("token email is not verified")

    return claims


# ---------------------------------------------------------------------------
# FastAPI dependency
# ---------------------------------------------------------------------------


def _extract_bearer(request: Request) -> str | None:
    """Return the bearer token from `Authorization`, or None if absent / malformed.

    Splits on the FIRST whitespace so a token with embedded spaces (which
    should never happen for OIDC, but defensively) is preserved as a
    single string. The scheme comparison is case-insensitive per RFC 7617.
    """
    header = request.headers.get("Authorization")
    if not header:
        return None
    parts = header.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    token = parts[1].strip()
    return token or None


def require_internal_oidc(
    request: Request,
    settings: Annotated[Settings, Depends(get_request_settings)],
) -> InternalPrincipal | None:
    """FastAPI dependency: enforce Google OIDC on `/internal/*` callers.

    - When `settings.require_internal_oidc` is False (the local default),
      this is a no-op returning None so dev / smoke scripts work without
      a Google token.
    - When it is True, the request must carry `Authorization: Bearer <id-token>`,
      the token must verify against `settings.internal_oidc_audience`,
      and the token's `email` claim must appear in
      `settings.internal_oidc_allowed_service_accounts`.
    - When OIDC is required but the audience or allowlist is missing
      (operator config drift), the dependency returns 503 -- failing
      closed rather than silently letting requests through.

    Error contract (all use the project's standard error envelope):
    - 401 `missing_bearer_token`              -- header absent / malformed
    - 401 `invalid_internal_oidc_token`       -- signature / audience / expiry fail
    - 401 `internal_oidc_token_missing_email` -- token verifies but has no email claim
    - 403 `internal_oidc_principal_forbidden` -- valid token, email not allowlisted
    - 503 `internal_oidc_misconfigured`       -- required but audience/allowlist unset
    """
    if not settings.require_internal_oidc:
        return None

    # Fail-closed config check happens BEFORE we look at the request,
    # so an operator who turns on require=True without configuring
    # audience + allowlist gets a 503 from EVERY call rather than
    # silently letting traffic through.
    if not settings.internal_oidc_audience:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="internal_oidc_misconfigured: audience unset",
        )
    if not settings.internal_oidc_allowed_service_accounts:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="internal_oidc_misconfigured: allowlist empty",
        )

    token = _extract_bearer(request)
    if token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing_bearer_token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        claims = verify_google_oidc_token(token, settings.internal_oidc_audience)
    except InternalAuthMisconfigured as exc:
        # Library missing -- alert operator to fix the deploy, not the
        # caller. Same status bucket as audience/allowlist drift.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"internal_oidc_misconfigured: {exc}",
        ) from None
    except InternalAuthError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid_internal_oidc_token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from None

    email_claim = claims.get("email")
    if not isinstance(email_claim, str) or not email_claim:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="internal_oidc_token_missing_email",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Google SA emails are issued lowercased; operators frequently paste
    # mixed-case copies from the Cloud Console or Terraform output. A
    # case mismatch would silently 403 a legitimate caller and Eventarc
    # would retry against the 403 forever, so we normalize both sides.
    email_normalized = email_claim.lower()
    allowed_normalized = {e.lower() for e in settings.internal_oidc_allowed_service_accounts}
    if email_normalized not in allowed_normalized:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="internal_oidc_principal_forbidden",
        )

    return InternalPrincipal(
        email=email_claim,
        audience=settings.internal_oidc_audience,
        claims=claims,
    )


InternalAuthDep = Annotated[InternalPrincipal | None, Depends(require_internal_oidc)]
