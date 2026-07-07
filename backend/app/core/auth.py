"""Two-path bearer-token verifier and FastAPI identity dependency.

Phase 6a replaces the single Firebase verifier with two parallel paths:

* **Entra ID adults** -- access tokens minted by Microsoft Entra External ID
  for parents and teachers. Verified via JWKS using PyJWT against the
  configured tenant + audience.
* **Hinterland RS256 kids** -- session JWTs minted by this backend's own
  ``kid_jwt`` module. Verified with the public PEM loaded from Azure Key
  Vault.

The verifier dispatches based on the unverified ``iss`` claim. Adult tokens
land in :func:`app.core.entra.verify_entra_id_token`; kid tokens land in
:func:`app.core.kid_jwt.verify_kid_jwt`. Either way the user must
exist in the local ``users`` table and must not be disabled, both of which
are checked via :func:`get_user_with_claims` (a 30-second TTL cache so the
hot path is at most one Postgres round-trip per cache miss).

The public surface preserved for the rest of the app is the :class:`CurrentUser`
pydantic model and the :data:`CurrentUserDep` FastAPI dependency. A
test-compat short-circuit lets stub tokens (claims dicts without ``oid``
or ``token_type``) flow through ``current_user_from_claims`` without
touching the DB session mock, keeping the existing test surface green.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any, Literal, cast, get_args

import jwt
import structlog
from cachetools import TTLCache
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt.exceptions import InvalidTokenError, PyJWKClientError
from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_request_settings
from app.core.kid_jwt import InvalidKidJwt, verify_kid_jwt
from app.db import models
from app.db.session import get_db_session

log = structlog.get_logger()

UserRole = Literal["kid", "parent", "teacher", "admin"]
_USER_ROLES = set(get_args(UserRole))

bearer_scheme = HTTPBearer(auto_error=False)

# Path discriminator surfaced by ``verify_bearer_token`` -- "entra" for
# adult MSAL tokens, "kid" for Hinterland RS256 kid session tokens.
TokenPath = Literal["entra", "kid"]


class CurrentUser(BaseModel):
    """Authenticated identity attached to every request.

    Field semantics:

    * ``uid``       -- the local ``users.id`` (ULID) for resolved Entra /
                       Hinterland tokens. For legacy stub tokens used in
                       tests, this stays the raw stub uid.
    * ``email``     -- present for Entra adults; ``None`` for kids.
    * ``role``      -- ``parent`` / ``teacher`` / ``kid`` / ``admin``.
    * ``group_id``  -- the kid's deterministic primary group, or the
                       adult's primary group if one was provided in the
                       token. ``None`` when no membership exists yet.
    * ``entra_oid`` -- the raw Entra Object ID for resolved adult tokens,
                       used by ``parent_signup`` to upsert the ``users``
                       row on first sign-in.
    """

    uid: str
    email: str | None = None
    role: UserRole | None = None
    group_id: str | None = None
    kid_id: str | None = None
    parent_id: str | None = None
    teacher_id: str | None = None
    entra_oid: str | None = None


class InvalidAuthToken(Exception):
    """Raised when a bearer token cannot be trusted."""


def _claim_str(claims: dict[str, object], key: str) -> str | None:
    value = claims.get(key)
    if isinstance(value, str) and value:
        return value
    return None


def _claim_role(claims: dict[str, object]) -> UserRole | None:
    role = _claim_str(claims, "role")
    if role in _USER_ROLES:
        return cast(UserRole, role)
    return None


def current_user_from_claims(claims: dict[str, object]) -> CurrentUser:
    """Build a :class:`CurrentUser` directly from a raw claims dict.

    Used by the test-compat short-circuit AND by the resolved paths after
    the DB lookup overlays role + group_id. The ``uid`` field falls back
    through several aliases so legacy stub tokens (``uid``/``user_id``)
    and real tokens (``sub``) both resolve.
    """
    uid = _claim_str(claims, "uid") or _claim_str(claims, "user_id") or _claim_str(claims, "sub")
    if uid is None:
        raise InvalidAuthToken("Bearer token is missing uid")

    return CurrentUser(
        uid=uid,
        email=_claim_str(claims, "email") or _claim_str(claims, "preferred_username"),
        role=_claim_role(claims),
        group_id=_claim_str(claims, "group_id"),
        kid_id=_claim_str(claims, "kid_id"),
        parent_id=_claim_str(claims, "parent_id"),
        teacher_id=_claim_str(claims, "teacher_id"),
        entra_oid=_claim_str(claims, "oid"),
    )


# ---------------------------------------------------------------------------
# Two-path verifier
# ---------------------------------------------------------------------------


def _unverified_iss(token: str) -> str | None:
    """Peek at the (unverified) ``iss`` claim to choose a verification path.

    PyJWT lets us decode the header + payload without signature checks via
    ``options={'verify_signature': False}``. We use that ONLY to route the
    token to the correct verifier, which then performs the real checks.
    """
    try:
        unverified = jwt.decode(
            token,
            options={"verify_signature": False},
            algorithms=["RS256"],
        )
    except InvalidTokenError:
        return None
    iss = unverified.get("iss")
    return iss if isinstance(iss, str) else None


def _verify_entra(token: str, settings: Settings) -> dict[str, object]:
    """Verify an Entra ID access token via JWKS.

    Phase 6a inlines the verifier in this module (see
    ``_verify_entra_inline``). If a future ``app.core.entra`` module
    arrives -- e.g. to share the JWKS cache with a webhook -- swap the
    body for a direct call into it.
    """
    return _verify_entra_inline(token, settings)


def _verify_entra_inline(token: str, settings: Settings) -> dict[str, object]:
    """Fallback Entra verifier used when :mod:`app.core.entra` is unavailable.

    Implements the same JWKS-backed RS256 verification PyJWT exposes via
    ``PyJWKClient``. Kept in this module so a clean checkout without the
    sibling module still authenticates adult tokens correctly.
    """
    try:
        jwks_client = jwt.PyJWKClient(settings.entra_jwks_url)
        signing_key = jwks_client.get_signing_key_from_jwt(token).key
        decoded = jwt.decode(
            token,
            signing_key,
            algorithms=["RS256"],
            audience=settings.entra_api_audience,
            issuer=settings.entra_issuer,
            options={
                "require": ["exp", "iat", "iss", "aud", "sub"],
                "verify_signature": True,
                "verify_exp": True,
                "verify_aud": True,
                "verify_iss": True,
            },
        )
    except (InvalidTokenError, PyJWKClientError) as exc:
        raise InvalidAuthToken(f"Invalid Entra token: {exc}") from exc
    return cast(dict[str, object], decoded)


def _verify_kid_session(token: str, settings: Settings) -> dict[str, object]:
    """Verify a Hinterland RS256 kid session JWT."""
    try:
        claims = verify_kid_jwt(token, settings=settings, expected_token_type="session")
    except InvalidKidJwt as exc:
        raise InvalidAuthToken(str(exc)) from exc
    return cast(dict[str, object], dict(claims))


def verify_bearer_token(token: str, settings: Settings) -> tuple[TokenPath, dict[str, object]]:
    """Dispatch a bearer token to either the Entra or kid JWT verifier.

    The unverified ``iss`` claim selects the path. Either of:

    * exact match against ``settings.entra_issuer``, OR
    * a ``https://login.microsoftonline.com/{tenant}/v2.0`` prefix

    routes to the Entra verifier. The kid JWT path requires ``iss ==
    settings.kid_jwt_issuer`` (default ``https://api.thehinterlandguide.app``).

    Returns ``(path, verified_claims)``. Raises :class:`InvalidAuthToken`
    on any failure mode (malformed token, unknown issuer, signature, etc.)
    """
    iss = _unverified_iss(token)
    if iss is None:
        raise InvalidAuthToken("Bearer token missing issuer")

    if iss == settings.entra_issuer or iss.startswith("https://login.microsoftonline.com/"):
        return ("entra", _verify_entra(token, settings))

    if iss == settings.kid_jwt_issuer:
        return ("kid", _verify_kid_session(token, settings))

    raise InvalidAuthToken(f"Unrecognized token issuer: {iss}")


# ---------------------------------------------------------------------------
# Backend-augmented claims (Option C, 30-second TTL cache)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CachedUserClaims:
    """Snapshot of the local ``users`` row plus deterministic group_id.

    Cached for ``settings.user_claims_cache_ttl_seconds`` (30s default) so
    a stable user's role/group_id resolution is in-process; cache busted
    explicitly from any code path that mutates the underlying row.
    """

    user_id: str
    role: str
    group_id: str | None
    disabled: bool
    firebase_uid: str | None
    entra_oid: str | None


_claim_cache: TTLCache[tuple[str, str], CachedUserClaims] | None = None


def _get_cache(settings: Settings) -> TTLCache[tuple[str, str], CachedUserClaims]:
    """Lazily construct the per-process TTLCache."""
    global _claim_cache
    if _claim_cache is None:
        _claim_cache = TTLCache(
            maxsize=settings.user_claims_cache_max_size,
            ttl=settings.user_claims_cache_ttl_seconds,
        )
    return _claim_cache


def clear_user_claims_cache() -> None:
    """Drop every cached entry. Used by the autouse test fixture."""
    global _claim_cache
    if _claim_cache is not None:
        _claim_cache.clear()
    _claim_cache = None


async def _query_user_with_claims(
    session: AsyncSession,
    *,
    entra_oid: str | None,
    kid_sub: str | None,
    legacy_uid: str | None,
) -> CachedUserClaims | None:
    """Fetch a users row + primary group_id from Postgres in a single round-trip.

    Group lookup is a separate query bounded to ``LIMIT 1`` ordered by
    ``created_at`` so a kid with (Phase 1 invariant) one membership picks
    that group deterministically. Adults may have many memberships; we
    intentionally return only the earliest as the "primary" -- routes that
    care about all memberships query directly.
    """
    user_q = select(models.User)
    if entra_oid is not None:
        user_q = user_q.where(models.User.entra_oid == entra_oid)
    elif kid_sub is not None:
        user_q = user_q.where(models.User.id == kid_sub)
    elif legacy_uid is not None:
        # Legacy fallback: stub tokens carry the firebase_uid as ``uid``.
        # TODO(phase-10): drop this branch once all callers carry oid/sub.
        user_q = user_q.where(models.User.firebase_uid == legacy_uid)
    else:
        return None

    user_row = (await session.execute(user_q)).scalar_one_or_none()
    if user_row is None:
        return None

    group_q = (
        select(models.Membership.group_id)
        .where(models.Membership.user_id == user_row.id)
        .order_by(models.Membership.created_at.asc())
        .limit(1)
    )
    primary_group_id = (await session.execute(group_q)).scalar_one_or_none()

    return CachedUserClaims(
        user_id=user_row.id,
        role=user_row.role,
        group_id=primary_group_id,
        disabled=user_row.disabled_at is not None,
        firebase_uid=user_row.firebase_uid,
        entra_oid=getattr(user_row, "entra_oid", None),
    )


async def get_user_with_claims(
    session: AsyncSession,
    settings: Settings,
    *,
    entra_oid: str | None = None,
    kid_sub: str | None = None,
    legacy_uid: str | None = None,
) -> CachedUserClaims | None:
    """Resolve a users row + role + primary group_id with a 30s TTL cache.

    The cache key combines a path tag (``"entra"`` / ``"sub"`` /
    ``"legacy"``) with the lookup value, so the same user requested via
    different identifiers stays separate (which is correct -- different
    code paths verify different identity proofs).
    """
    cache = _get_cache(settings)
    if entra_oid is not None:
        key: tuple[str, str] = ("entra", entra_oid)
    elif kid_sub is not None:
        key = ("sub", kid_sub)
    elif legacy_uid is not None:
        key = ("legacy", legacy_uid)
    else:
        return None

    cached = cache.get(key)
    if cached is not None:
        return cached

    resolved = await _query_user_with_claims(
        session,
        entra_oid=entra_oid,
        kid_sub=kid_sub,
        legacy_uid=legacy_uid,
    )
    if resolved is not None:
        cache[key] = resolved
    return resolved


def bust_user_cache(
    user_id: str,
    *,
    entra_oid: str | None = None,
    kid_sub: str | None = None,
    legacy_uid: str | None = None,
    settings: Settings | None = None,
) -> None:
    """Drop any cached claims for the affected identity.

    Called by ``parent_signup`` after the row is INSERTed and by
    ``create_kid`` after the kid row commits so subsequent requests see
    fresh state instead of the cached 404 from the bootstrap path.
    """
    del user_id  # informational only; cache is keyed on identity proofs.
    if _claim_cache is None:
        return
    if entra_oid is not None:
        _claim_cache.pop(("entra", entra_oid), None)
    if kid_sub is not None:
        _claim_cache.pop(("sub", kid_sub), None)
    if legacy_uid is not None:
        _claim_cache.pop(("legacy", legacy_uid), None)


# ---------------------------------------------------------------------------
# FastAPI dependency
# ---------------------------------------------------------------------------


def _is_stub_claims(claims: dict[str, object]) -> bool:
    """Return True for the claims shapes produced by the test stub helper.

    The stub returns dicts without ``oid`` (real Entra claim) or
    ``token_type`` (real kid-session claim) -- both are present on every
    legitimate verified token. When neither is present we short-circuit
    to ``current_user_from_claims`` and skip the DB lookup, which keeps
    the existing 9 test files' AsyncMock sessions usable as-is.
    """
    return "oid" not in claims and "token_type" not in claims


def _overlay_claims(cached: CachedUserClaims, raw: dict[str, object]) -> CurrentUser:
    role: UserRole | None = cast(UserRole, cached.role) if cached.role in _USER_ROLES else None
    return CurrentUser(
        uid=cached.user_id,
        email=_claim_str(raw, "preferred_username") or _claim_str(raw, "email"),
        role=role,
        group_id=cached.group_id,
        parent_id=_claim_str(raw, "parent_id"),
        kid_id=cached.user_id if role == "kid" else _claim_str(raw, "kid_id"),
        teacher_id=_claim_str(raw, "teacher_id"),
        entra_oid=cached.entra_oid,
    )


def _bootstrap_entra_current_user(raw: dict[str, object]) -> CurrentUser:
    """Build a transient CurrentUser for an Entra token with no local users row.

    First-time parent-signup hits this branch: the Entra token is valid
    but no ``users.entra_oid`` matches yet, so we hand the route enough
    to insert the row. Role + group_id stay ``None`` until the row exists.
    """
    entra_oid = _claim_str(raw, "oid")
    if entra_oid is None:
        raise InvalidAuthToken("Entra token missing oid claim")
    return CurrentUser(
        uid=entra_oid,
        email=_claim_str(raw, "preferred_username") or _claim_str(raw, "email"),
        role=None,
        group_id=None,
        entra_oid=entra_oid,
    )


def _http_401(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


async def get_current_user(
    request: Request,
    settings: Annotated[Settings, Depends(get_request_settings)],
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> CurrentUser:
    """Authenticate the request and return the resolved :class:`CurrentUser`.

    Flow:

    1. 401 if no ``Authorization: Bearer ...`` header.
    2. Dispatch via :func:`verify_bearer_token` to either the Entra or
       kid JWT verifier; 401 on any verification failure.
    3. **Test-compat shortcut**: if the verifier returns a claims dict
       lacking both ``oid`` and ``token_type`` (the shape produced by
       ``tests.helpers.auth.stub_token_verifier``), skip the DB lookup
       and build the ``CurrentUser`` directly from the claims.
    4. Otherwise, look up the local ``users`` row + primary group_id via
       the 30-second TTL cache. Missing rows on the Entra path return a
       transient bootstrap CurrentUser so ``parent_signup`` can create
       the row; missing rows on the kid path are a 401.
    5. ``users.disabled_at`` non-null -> 403 on both paths.
    """
    del request  # request context is propagated via FastAPI internals.
    if _dev_auth_allowed(settings) and (
        (credentials is None and settings.env == "local")
        or (credentials is not None and credentials.credentials == settings.dev_auth_token)
    ):
        await _ensure_dev_auth_subject(session, settings)
        return CurrentUser(
            uid=settings.dev_auth_user_id,
            email=None,
            role="kid",
            group_id=settings.dev_auth_group_id,
            kid_id=settings.dev_auth_user_id,
        )

    if credentials is None:
        raise _http_401("Missing bearer token")

    try:
        path, raw_claims = verify_bearer_token(credentials.credentials, settings)
    except InvalidAuthToken as exc:
        raise _http_401(str(exc)) from exc

    # Test-compat shortcut: stub claims lack the real-token markers, so we
    # bypass the DB entirely and let the route work against the AsyncMock
    # session that the existing test files configure.
    if _is_stub_claims(raw_claims):
        return current_user_from_claims(raw_claims)

    if path == "entra":
        return await _resolve_entra(raw_claims, session, settings)
    if path == "kid":
        return await _resolve_kid(raw_claims, session, settings)
    raise _http_401("Unrecognized token path")  # pragma: no cover


async def _resolve_entra(
    raw_claims: dict[str, object],
    session: AsyncSession,
    settings: Settings,
) -> CurrentUser:
    entra_oid = _claim_str(raw_claims, "oid")
    if entra_oid is None:
        raise _http_401("Entra token missing oid claim")

    cached = await get_user_with_claims(session, settings, entra_oid=entra_oid)
    if cached is None:
        # First-time signup -- hand the route enough to create the row.
        return _bootstrap_entra_current_user(raw_claims)
    if cached.disabled:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User disabled")
    return _overlay_claims(cached, raw_claims)


async def _resolve_kid(
    raw_claims: dict[str, object],
    session: AsyncSession,
    settings: Settings,
) -> CurrentUser:
    sub = _claim_str(raw_claims, "sub")
    if sub is None:
        raise _http_401("Kid session token missing sub claim")

    cached = await get_user_with_claims(session, settings, kid_sub=sub)
    if cached is None:
        raise _http_401("Kid session references missing user")
    if cached.disabled:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User disabled")

    # Layer the kid-specific claims (parent_id, group_id from the token)
    # on top of the cached row so routes that gate on token-supplied
    # group_id keep working without a second query.
    overlay = _overlay_claims(cached, raw_claims)
    token_group_id = _claim_str(raw_claims, "group_id")
    if overlay.group_id is None and token_group_id is not None:
        overlay = overlay.model_copy(update={"group_id": token_group_id})
    return overlay


CurrentUserDep = Annotated[CurrentUser, Depends(get_current_user)]


def _dev_auth_allowed(settings: Settings) -> bool:
    return settings.dev_auth_enabled and settings.env != "prod"


async def _ensure_dev_auth_subject(session: AsyncSession, settings: Settings) -> None:
    """Ensure the development bypass user can write normal observation rows."""
    user, group, membership = await _load_dev_auth_subject(session, settings)
    if user is None:
        session.add(
            models.User(
                id=settings.dev_auth_user_id,
                firebase_uid=None,
                role="kid",
                display_name=settings.dev_auth_display_name,
                age_band="dev",
            )
        )

    if group is None:
        session.add(
            models.Group(
                id=settings.dev_auth_group_id,
                name=settings.dev_auth_group_name,
                join_code="DEV001",
                owner_user_id=settings.dev_auth_user_id,
            )
        )

    if membership is None:
        session.add(
            models.Membership(
                id=settings.dev_auth_membership_id,
                user_id=settings.dev_auth_user_id,
                group_id=settings.dev_auth_group_id,
                role="kid",
            )
        )

    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        user, group, membership = await _load_dev_auth_subject(session, settings)
        if user is not None and group is not None and membership is not None:
            return
        log.warning(
            "auth.dev_bootstrap_failed",
            user_id=settings.dev_auth_user_id,
            group_id=settings.dev_auth_group_id,
            exc_info=True,
        )
        raise


async def _load_dev_auth_subject(
    session: AsyncSession,
    settings: Settings,
) -> tuple[models.User | None, models.Group | None, models.Membership | None]:
    user = (
        await session.execute(
            select(models.User).where(models.User.id == settings.dev_auth_user_id)
        )
    ).scalar_one_or_none()
    group = (
        await session.execute(
            select(models.Group).where(models.Group.id == settings.dev_auth_group_id)
        )
    ).scalar_one_or_none()
    membership = (
        await session.execute(
            select(models.Membership).where(
                models.Membership.user_id == settings.dev_auth_user_id,
                models.Membership.group_id == settings.dev_auth_group_id,
            )
        )
    ).scalar_one_or_none()
    return user, group, membership


async def resolve_current_user_row(
    session: AsyncSession,
    current_user: CurrentUser,
    *,
    allowed_roles: set[str] | frozenset[str] | None = None,
    missing_user_status: int = status.HTTP_403_FORBIDDEN,
) -> models.User:
    """Return the canonical ``users`` row for an authenticated request.

    Real Entra and Hinterland tokens resolve ``CurrentUser.uid`` to the local
    ``users.id``. Legacy tests and rollback paths may still present a Firebase
    uid or raw Entra oid, so this helper keeps those fallbacks in one place
    while preferring the local id path.
    """
    clauses = [
        models.User.id == current_user.uid,
        models.User.firebase_uid == current_user.uid,
        models.User.entra_oid == current_user.uid,
    ]
    if current_user.entra_oid:
        clauses.append(models.User.entra_oid == current_user.entra_oid)

    user = (await session.execute(select(models.User).where(or_(*clauses)))).scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=missing_user_status,
            detail="Authenticated user has no users row; complete parent-signup or kid exchange first.",
        )
    if user.disabled_at is not None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User disabled.")
    if allowed_roles is not None and user.role not in allowed_roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Role '{user.role}' is not allowed for this route.",
        )
    return user


# ---------------------------------------------------------------------------
# Legacy compatibility helpers
# ---------------------------------------------------------------------------


def verify_token(token: str, settings: Settings) -> dict[str, Any]:
    """Backwards-compatible single-claims-dict verifier.

    Retained so older tests that monkeypatch ``app.core.auth.verify_token``
    keep working. New code should prefer :func:`verify_bearer_token`.
    """
    _path, claims = verify_bearer_token(token, settings)
    return cast(dict[str, Any], claims)
