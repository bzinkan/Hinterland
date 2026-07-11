"""Authenticated user routes."""

from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime, timedelta
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID

from app.core.auth import (
    CurrentUser,
    CurrentUserDep,
    bust_user_cache,
    resolve_current_user_row,
)
from app.core.config import Settings, get_request_settings
from app.core.kid_jwt import (
    InvalidHinterlandJwt,
    mint_session_token,
    public_jwks,
    verify_hinterland_jwt,
)
from app.core.parent_consent import (
    CURRENT_PARENT_CONSENT_POLICY_VERSION,
    CURRENT_PARENT_CONSENT_REQUIRED_MESSAGE,
    CurrentParentConsentRequiredError,
    acquire_current_parent_consent,
    hash_browser_consent_nonce,
)
from app.db import models
from app.db.session import DbSessionDep

router = APIRouter(prefix="/v1", tags=["auth"])

# JWKS lives at /.well-known/... -- no /v1 prefix. Mounted separately from
# the main auth router so FastAPI doesn't prepend the prefix.
well_known_router = APIRouter(tags=["auth"])

log = structlog.get_logger()

# Bumped any time the privacy policy text changes materially. Recorded
# alongside each consent so we know which version each parent agreed to.
_CURRENT_POLICY_VERSION = CURRENT_PARENT_CONSENT_POLICY_VERSION


class ParentSignupRequest(BaseModel):
    display_name: str = Field(..., min_length=1, max_length=80)
    consent_id: str = Field(..., min_length=26, max_length=26)
    consent_nonce: str = Field(..., pattern=r"^[0-9a-f]{64}$")


class UserResponse(BaseModel):
    """Public shape of a `users` row over the API."""

    id: str
    firebase_uid: str | None = None
    entra_oid: str | None = None
    role: str
    display_name: str

    @classmethod
    def from_model(cls, user: models.User) -> UserResponse:
        return cls(
            id=user.id,
            firebase_uid=user.firebase_uid,
            entra_oid=getattr(user, "entra_oid", None),
            role=user.role,
            display_name=user.display_name,
        )


class AccountDeletionResponse(BaseModel):
    status: str
    user_id: str
    requested_at: datetime


async def _load_parent_by_identity(
    session: AsyncSession,
    *,
    entra_oid: str | None,
    legacy_uid: str,
) -> models.User | None:
    """Load the canonical adult row using the verified token identity."""
    if entra_oid is not None:
        statement = select(models.User).where(models.User.entra_oid == entra_oid)
    else:
        statement = select(models.User).where(models.User.firebase_uid == legacy_uid)
    result = await session.execute(statement)
    return result.scalar_one_or_none()


def _integrity_constraint_name(exc: IntegrityError) -> str | None:
    """Read a driver constraint name without parsing database error text."""
    current: object | None = exc.orig
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        constraint_name = getattr(current, "constraint_name", None)
        if isinstance(constraint_name, str):
            return constraint_name
        diag = getattr(current, "diag", None)
        diagnostic_name = getattr(diag, "constraint_name", None)
        if isinstance(diagnostic_name, str):
            return diagnostic_name
        current = getattr(current, "__cause__", None)
    return None


def _is_expected_parent_identity_conflict(
    exc: IntegrityError,
    *,
    entra_oid: str | None,
) -> bool:
    expected = "uq_users_entra_oid" if entra_oid is not None else "uq_users_firebase_uid"
    return _integrity_constraint_name(exc) == expected


@router.get("/me", response_model=CurrentUser)
def me(current_user: CurrentUserDep) -> CurrentUser:
    return current_user


@router.delete("/me", response_model=AccountDeletionResponse)
async def request_account_deletion(
    current_user: CurrentUserDep,
    session: DbSessionDep,
) -> AccountDeletionResponse:
    """Disable the authenticated account and record the deletion request.

    This is the app-store-visible one-tap path. The immediate effect is
    fail-closed auth on the next request via ``users.disabled_at``, and the
    user's expedition progress is erased immediately in the same transaction;
    full data erasure for linked kid accounts/photos/iNat contributions
    remains the documented human follow-up for beta operations.
    """
    requested_at = datetime.now(UTC)
    user = await resolve_current_user_row(session, current_user)
    user.disabled_at = requested_at
    # COPPA: expedition progress is per-user gameplay data with no audit
    # value -- purge it in the same transaction as the disable so the
    # deletion request leaves no progress rows behind. RETURNING gives
    # the purged count for the structured event without a second query.
    purged = (
        await session.execute(
            delete(models.ExpeditionProgress)
            .where(models.ExpeditionProgress.user_id == user.id)
            .returning(models.ExpeditionProgress.id)
        )
    ).all()
    await session.commit()
    bust_user_cache(
        user.id,
        entra_oid=getattr(user, "entra_oid", None),
        hinterland_sub=user.id if user.role == "kid" else None,
        legacy_uid=user.firebase_uid,
    )
    log.info(
        "auth.account_deletion_requested",
        user_id=user.id,
        role=user.role,
        requested_at=requested_at.isoformat(),
        expedition_progress_purged=len(purged),
    )
    return AccountDeletionResponse(
        status="deletion_requested",
        user_id=user.id,
        requested_at=requested_at,
    )


@router.post(
    "/auth/parent-signup",
    response_model=UserResponse,
    status_code=status.HTTP_200_OK,
)
async def parent_signup(
    request_body: ParentSignupRequest,
    current_user: CurrentUserDep,
    session: DbSessionDep,
    settings: Annotated[Settings, Depends(get_request_settings)],
) -> UserResponse:
    """Create or return the parent `users` row for the authenticated Entra ID.

    The client is expected to have already signed in with Microsoft Entra
    External ID via MSAL. This endpoint:

    1. Reads the verified Entra access token from the `Authorization` header
       (handled by the CurrentUserDep dependency).
    2. Upserts a `users` row with `role='parent'` keyed by the Entra OID
       (`users.entra_oid`).

    Idempotent: if a `users` row already exists for this entra_oid, the
    existing row is returned and no new `users` row is created. For legacy row
    compatibility we also fall back to looking up by `firebase_uid` when the
    caller has no Entra OID -- this keeps existing test stubs working until the
    column is removed by a future audited migration.
    """
    # Resolve the Entra OID. In production this comes from the verified
    # token; legacy/test paths may put the compatibility uid into
    # current_user.uid with entra_oid=None.
    entra_oid = current_user.entra_oid

    existing = await _load_parent_by_identity(
        session,
        entra_oid=entra_oid,
        legacy_uid=current_user.uid,
    )

    if existing is not None:
        # Backfill entra_oid on legacy rows whose first sign-in is via Entra.
        identity_updated = False
        if entra_oid is not None and getattr(existing, "entra_oid", None) is None:
            existing.entra_oid = entra_oid
            identity_updated = True
        try:
            consent = await acquire_current_parent_consent(
                session,
                parent_user_id=existing.id,
                verified_email=current_user.email,
                consent_id=request_body.consent_id,
                consent_nonce=request_body.consent_nonce,
            )
        except CurrentParentConsentRequiredError as exc:
            await session.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=CURRENT_PARENT_CONSENT_REQUIRED_MESSAGE,
            ) from exc
        if identity_updated or consent.newly_linked:
            await session.commit()
        log.info(
            "auth.parent_signup.idempotent",
            user_id=existing.id,
            entra_oid=entra_oid,
            firebase_uid=existing.firebase_uid,
            linked_consent_id=consent.record.id,
        )
        return UserResponse.from_model(existing)

    # New row: Entra-only, no legacy external identity for fresh signups.
    new_user = models.User(
        id=str(ULID()),
        firebase_uid=None,
        role="parent",
        display_name=request_body.display_name,
    )
    if entra_oid is not None:
        new_user.entra_oid = entra_oid
    else:
        # Legacy stub path: keep the compatibility uid populated so existing
        # test assertions that read `added_user.firebase_uid` still see a
        # value. Real Entra signups land in the branch above.
        new_user.firebase_uid = current_user.uid

    # Flush the canonical user first so the consent ledger's FK update can
    # only follow a valid users.id INSERT.  This remains one transaction: a
    # missing receipt rolls the provisional user back below.
    session.add(new_user)
    try:
        await session.flush()
    except IntegrityError as exc:
        # Two first-time requests for the same verified identity can both
        # miss the initial read. Only the expected identity uniqueness race
        # is recoverable; every other constraint failure is re-raised after
        # restoring the session. The winning user must still present and
        # revalidate this exact browser-bound consent proof.
        await session.rollback()
        if not _is_expected_parent_identity_conflict(exc, entra_oid=entra_oid):
            raise

        winner = await _load_parent_by_identity(
            session,
            entra_oid=entra_oid,
            legacy_uid=current_user.uid,
        )
        if winner is None:
            raise
        try:
            consent = await acquire_current_parent_consent(
                session,
                parent_user_id=winner.id,
                verified_email=current_user.email,
                consent_id=request_body.consent_id,
                consent_nonce=request_body.consent_nonce,
            )
        except CurrentParentConsentRequiredError as proof_exc:
            await session.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=CURRENT_PARENT_CONSENT_REQUIRED_MESSAGE,
            ) from proof_exc
        if consent.newly_linked:
            await session.commit()
        log.info(
            "auth.parent_signup.concurrent_replay",
            user_id=winner.id,
            entra_oid=entra_oid,
            linked_consent_id=consent.record.id,
        )
        return UserResponse.from_model(winner)
    try:
        consent = await acquire_current_parent_consent(
            session,
            parent_user_id=new_user.id,
            verified_email=current_user.email,
            consent_id=request_body.consent_id,
            consent_nonce=request_body.consent_nonce,
        )
    except CurrentParentConsentRequiredError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=CURRENT_PARENT_CONSENT_REQUIRED_MESSAGE,
        ) from exc

    # The already-flushed user INSERT and consent-link UPDATE commit together.
    await session.commit()
    await session.refresh(new_user)

    log.info(
        "auth.parent_signup.created",
        user_id=new_user.id,
        entra_oid=entra_oid,
        linked_consent_id=consent.record.id,
    )
    return UserResponse.from_model(new_user)


# ---------------------------------------------------------------------------
# POST /v1/auth/kid-exchange -- swap a single-use handoff JWT for a session JWT
# ---------------------------------------------------------------------------


class KidExchangeRequest(BaseModel):
    handoff_token: str = Field(..., min_length=1, max_length=4096)


class KidExchangeResponse(BaseModel):
    session_token: str
    expires_at: datetime
    user: UserResponse


@router.post(
    "/auth/kid-exchange",
    response_model=KidExchangeResponse,
    status_code=status.HTTP_200_OK,
)
async def kid_exchange(
    payload: KidExchangeRequest,
    session: DbSessionDep,
    settings: Annotated[Settings, Depends(get_request_settings)],
) -> KidExchangeResponse:
    """Exchange a single-use handoff JWT for a long-lived kid session JWT.

    PUBLIC endpoint -- no `Authorization` header. The handoff JWT in the
    request body IS the proof of authority; it was minted by the parent's
    `POST /v1/groups/{group_id}/kids` call and handed to the kid's device.

    Single-use is enforced by an atomic INSERT into `kid_handoff_jti` keyed
    on the JWT's `jti` claim: a unique-violation means the token was already
    redeemed and we return 409 Conflict.
    """
    # 1. Verify the JWT signature + claims (issuer, audience, expiry, type).
    try:
        claims = verify_hinterland_jwt(
            payload.handoff_token,
            settings=settings,
            expected_token_type="handoff",
        )
    except InvalidHinterlandJwt as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid handoff token: {exc}",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    jti_value = claims.get("jti")
    sub_value = claims.get("sub")
    exp_value = claims.get("exp")
    if not isinstance(jti_value, str) or not isinstance(sub_value, str) or exp_value is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Handoff token missing required claims",
            headers={"WWW-Authenticate": "Bearer"},
        )
    jti = jti_value
    kid_user_id = sub_value
    expires_at = datetime.fromtimestamp(int(exp_value), tz=UTC)

    parent_id_claim = claims.get("parent_id")
    parent_id = parent_id_claim if isinstance(parent_id_claim, str) else ""
    group_id_claim = claims.get("group_id")
    group_id = group_id_claim if isinstance(group_id_claim, str) else ""

    # 2. Atomic single-use: INSERT the jti. Unique-PK collision means
    #    this handoff was already redeemed (replay attempt).
    jti_row = models.KidHandoffJti(
        jti=jti,
        kid_user_id=kid_user_id,
        consumed_at=datetime.now(UTC),
        expires_at=expires_at,
    )
    session.add(jti_row)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        log.info("auth.kid_exchange.replay", jti=jti, kid_id=kid_user_id)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Handoff token already used",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    # 3. Load the kid's users row.
    kid_result = await session.execute(select(models.User).where(models.User.id == kid_user_id))
    kid = kid_result.scalar_one_or_none()
    if kid is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Kid user not found.",
        )
    if kid.disabled_at is not None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User disabled.",
        )

    # 4. Mint the session JWT (30 days by default; settings-driven).
    session_token = mint_session_token(
        kid_user_id=kid.id,
        parent_id=parent_id,
        group_id=group_id,
        settings=settings,
    )
    session_expires_at = datetime.now(UTC) + timedelta(
        seconds=settings.hinterland_session_ttl_seconds
    )

    log.info(
        "auth.kid_exchange.success",
        kid_id=kid.id,
        jti=jti,
        parent_id=parent_id,
        group_id=group_id,
    )

    return KidExchangeResponse(
        session_token=session_token,
        expires_at=session_expires_at,
        user=UserResponse.from_model(kid),
    )


# ---------------------------------------------------------------------------
# POST /v1/auth/dev-login -- silent auto-login for pre-production builds
# ---------------------------------------------------------------------------
#
# The dev API and the W1 pilot share one deployment, which is why this
# route is fail-closed three independent ways (see `_dev_login_available`):
# default-off flag, mandatory non-empty shared key, and an unconditional
# 404 on env=prod. When closed, the response is byte-identical to an
# unregistered route (404 "Not Found"), and the route is excluded from
# the OpenAPI schema, so probing cannot tell the feature exists.

# Fixed, well-known ULIDs for the idempotent dev sandbox lineage. All four
# are valid 26-char Crockford base32 ULIDs (alphabet excludes I L O U) so
# they behave exactly like organically-minted ids everywhere downstream.
# Being module constants (not minted per call) is what makes provisioning
# idempotent: every dev-login call converges on the same four rows and can
# never touch real rows.
DEV_PARENT_USER_ID = "01JZDEV0PARENT000000000000"
DEV_GROUP_ID = "01JZDEV0GRP000000000000000"
DEV_KID_USER_ID = "01JZDEV0K1D000000000000000"
DEV_MEMBERSHIP_ID = "01JZDEV0MEMB00000000000000"
DEV_PARENT_MEMBERSHIP_ID = "01JZDEV0MEMBPAR00000000000"

# Crockford base32 alphabet (no I L O U) -- the shape
# generate_join_code() produces.
_JOIN_CODE_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def _dev_join_code(dev_login_key: str) -> str:
    """Deterministic 6-char join code derived from the dev-login key.

    A fixed literal here would be a well-known code any authenticated
    user could use to join the sandbox group (the source is on GitHub).
    Deriving from the deployment's secret key keeps provisioning
    idempotent per deployment while making the code unguessable without
    the key. Rotating the key does NOT rotate an already-provisioned
    group's code (the group row is found by PK and left as-is).
    """
    digest = hashlib.sha256(f"dev-join-code:{dev_login_key}".encode()).digest()
    return "".join(_JOIN_CODE_ALPHABET[b % 32] for b in digest[:6])


def _dev_login_available(settings: Settings) -> bool:
    """True only when every dev-login gate is open.

    Belt-and-braces ordering: prod wins over everything, then the
    default-off flag, then the mandatory key. Enabled-without-key is a
    misconfiguration that must never open the door -- it stays 404 and
    logs a warning so the operator can spot it.
    """
    if settings.env == "prod":
        return False
    if not settings.dev_login_enabled:
        return False
    if not settings.dev_login_key:
        log.warning("auth.dev_login.enabled_without_key", env=settings.env)
        return False
    return True


async def _get_or_create_dev_rows(session: AsyncSession, dev_login_key: str) -> models.User:
    """Get-or-create the fixed dev sandbox lineage; returns the dev kid row.

    Each row is looked up by its well-known PK and only inserted when
    missing, so the whole call is idempotent. Flush ordering mirrors
    groups.create_kid: FK targets must exist before their dependents in
    the same session.

    Existing rows are HEALED, not just fetched: the sandbox is synthetic,
    and a single "Request account deletion" tap from inside it would
    otherwise set disabled_at and brick dev-login for the whole
    deployment (the kid loader 403s disabled users).
    """
    parent = await session.get(models.User, DEV_PARENT_USER_ID)
    if parent is None:
        parent = models.User(
            id=DEV_PARENT_USER_ID,
            firebase_uid=None,
            entra_oid=None,
            role="parent",
            display_name="Dev Parent",
        )
        session.add(parent)
        await session.flush()
    elif parent.disabled_at is not None:
        parent.disabled_at = None

    group = await session.get(models.Group, DEV_GROUP_ID)
    if group is None:
        group = models.Group(
            id=DEV_GROUP_ID,
            name="Dev Group",
            join_code=_dev_join_code(dev_login_key),
            owner_user_id=DEV_PARENT_USER_ID,
        )
        session.add(group)
        await session.flush()

    kid = await session.get(models.User, DEV_KID_USER_ID)
    if kid is None:
        kid = models.User(
            id=DEV_KID_USER_ID,
            firebase_uid=None,
            entra_oid=None,
            role="kid",
            display_name="Dev Kid",
            age_band="11-12",
            parent_user_id=DEV_PARENT_USER_ID,
            # Pre-consented sandbox: the dev lineage exists only on
            # non-prod deployments and never represents a real child.
            consent_granted_at=datetime.now(UTC),
        )
        session.add(kid)
        await session.flush()
    else:
        if kid.disabled_at is not None:
            kid.disabled_at = None
        if kid.consent_granted_at is None:
            kid.consent_granted_at = datetime.now(UTC)

    membership = await session.get(models.Membership, DEV_MEMBERSHIP_ID)
    if membership is None:
        membership = models.Membership(
            id=DEV_MEMBERSHIP_ID,
            group_id=DEV_GROUP_ID,
            user_id=DEV_KID_USER_ID,
            role="kid",
        )
        session.add(membership)

    # Organic create_group gives the owner a Membership row too; group
    # listing and role checks assume it exists.
    parent_membership = await session.get(models.Membership, DEV_PARENT_MEMBERSHIP_ID)
    if parent_membership is None:
        parent_membership = models.Membership(
            id=DEV_PARENT_MEMBERSHIP_ID,
            group_id=DEV_GROUP_ID,
            user_id=DEV_PARENT_USER_ID,
            role="parent",
        )
        session.add(parent_membership)

    await session.commit()
    return kid


async def _ensure_dev_sandbox(session: AsyncSession, dev_login_key: str) -> models.User:
    """Idempotently provision the dev sandbox, tolerating a concurrent first call."""
    try:
        return await _get_or_create_dev_rows(session, dev_login_key)
    except IntegrityError:
        # Two racing first calls both saw missing rows and both inserted;
        # the loser lands here. Roll back and re-run -- the second pass
        # re-fetches by PK and only inserts whatever is still missing.
        await session.rollback()
        log.info("auth.dev_login.provision_race_retry")
        return await _get_or_create_dev_rows(session, dev_login_key)


@router.post(
    "/auth/dev-login",
    response_model=KidExchangeResponse,
    status_code=status.HTTP_200_OK,
    include_in_schema=False,
)
async def dev_login(
    session: DbSessionDep,
    settings: Annotated[Settings, Depends(get_request_settings)],
    x_dev_login_key: Annotated[str | None, Header()] = None,
) -> KidExchangeResponse:
    """Mint a kid session JWT for the fixed dev sandbox lineage.

    Pre-production mobile builds call this at boot (silently, no UI) so
    developers land signed-in without the QR handoff dance. Store builds
    never carry the key and the route 404s unless explicitly enabled --
    see `_dev_login_available` for the fail-closed gate stack.

    Returns the exact `KidExchangeResponse` shape so the mobile client
    reuses the kid-exchange response type unchanged.
    """
    if not _dev_login_available(settings):
        # Indistinguishable from an unregistered route.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")

    supplied_key = x_dev_login_key or ""
    configured_key = settings.dev_login_key or ""
    if not supplied_key or not hmac.compare_digest(
        supplied_key.encode("utf-8"), configured_key.encode("utf-8")
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid dev-login key.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    kid = await _ensure_dev_sandbox(session, configured_key)

    session_token = mint_session_token(
        kid_user_id=DEV_KID_USER_ID,
        parent_id=DEV_PARENT_USER_ID,
        group_id=DEV_GROUP_ID,
        settings=settings,
    )
    session_expires_at = datetime.now(UTC) + timedelta(
        seconds=settings.hinterland_session_ttl_seconds
    )

    log.info(
        "auth.dev_login.success",
        kid_id=DEV_KID_USER_ID,
        parent_id=DEV_PARENT_USER_ID,
        group_id=DEV_GROUP_ID,
    )

    return KidExchangeResponse(
        session_token=session_token,
        expires_at=session_expires_at,
        user=UserResponse.from_model(kid),
    )


# ---------------------------------------------------------------------------
# GET public JWKS for kid tokens
# ---------------------------------------------------------------------------


@well_known_router.get(
    "/.well-known/hinterland-kid-jwks.json",
    include_in_schema=False,
)
def kid_jwks(
    response: Response,
    settings: Annotated[Settings, Depends(get_request_settings)],
) -> dict[str, object]:
    """Return the Hinterland kid-JWT signing key in JWKS format.

    Used by any downstream service (mobile app, future services) to verify
    Hinterland-minted kid handoff / session tokens. The same kid is rotated
    rarely (manifest constant `hinterland_jwt_kid`), so this response is
    cacheable for an hour.
    """
    response.headers["Cache-Control"] = "public, max-age=3600"
    return public_jwks(settings)


# ---------------------------------------------------------------------------
# POST /v1/auth/consent -- public, COPPA parental consent record
# ---------------------------------------------------------------------------


class ConsentRequest(BaseModel):
    """Pre-signup consent record. Public endpoint -- no auth header.

    The parent visits the web /consent page, enters their email,
    optionally enters the kid's display name, and confirms. We persist
    a `parent_consent_records` row AND emit the structured log event
    so existing log-based audits still work. The row is the long-term
    source of truth; the log is the operational signal.

    When the parent later signs up, the client must return both this exact
    receipt ID and the original browser nonce. The server cross-checks the
    authenticated Entra email and current policy before linking the receipt.
    """

    # Lightweight regex check -- avoids pulling in email-validator just
    # for one endpoint. Real semantic validation happens via Entra at
    # signup time.
    email: str = Field(..., pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", max_length=320)
    kid_display_name: str | None = Field(default=None, max_length=80)
    policy_version: str = Field(..., min_length=1, max_length=64)
    consent_nonce: str = Field(..., pattern=r"^[0-9a-f]{64}$")


class ConsentResponse(BaseModel):
    id: str
    recorded_at: datetime
    policy_version: str


def _consent_replay_matches(
    record: models.ParentConsentRecord,
    *,
    normalized_email: str,
) -> bool:
    return (
        record.parent_email.strip().lower() == normalized_email
        and record.policy_version == _CURRENT_POLICY_VERSION
    )


def _consent_response(record: models.ParentConsentRecord) -> ConsentResponse:
    return ConsentResponse(
        id=record.id,
        recorded_at=record.recorded_at,
        policy_version=record.policy_version,
    )


async def _load_consent_by_nonce_hash(
    session: AsyncSession,
    nonce_hash: str,
) -> models.ParentConsentRecord | None:
    result = await session.execute(
        select(models.ParentConsentRecord).where(
            models.ParentConsentRecord.browser_nonce_sha256 == nonce_hash
        )
    )
    return result.scalar_one_or_none()


def _raise_consent_replay_conflict() -> None:
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="Consent nonce was already used with different details",
    )


@router.post(
    "/auth/consent",
    response_model=ConsentResponse,
    status_code=status.HTTP_200_OK,
)
async def record_consent(
    payload: ConsentRequest,
    response: Response,
    session: DbSessionDep,
) -> ConsentResponse:
    """Record COPPA parental consent. Public, unauthenticated.

    Storage shape today:
      1. INSERTs a `parent_consent_records` row -- the durable audit
         ledger -- with only the SHA-256 digest of the 256-bit browser
         nonce. The raw nonce remains in browser memory/storage for signup.
      2. Emits `auth.consent.recorded` to structured logs with the
         new row id so the existing log-based ops dashboards keep
         working.

    A retry using the same nonce, normalized email, and current policy returns
    HTTP 200 with the original response and `Idempotency-Replayed: true`.
    Reusing the nonce with a different email or policy fails with 409. Parent
    signup must present the exact response ID and raw nonce; email alone can
    never claim a receipt.
    """
    if payload.policy_version != _CURRENT_POLICY_VERSION:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Consent policy version is not current",
        )
    version = _CURRENT_POLICY_VERSION
    normalized_email = payload.email.strip().lower()
    nonce_hash = hash_browser_consent_nonce(payload.consent_nonce)
    existing = await _load_consent_by_nonce_hash(session, nonce_hash)
    if existing is not None:
        if not _consent_replay_matches(
            existing,
            normalized_email=normalized_email,
        ):
            _raise_consent_replay_conflict()
        response.headers["Idempotency-Replayed"] = "true"
        return _consent_response(existing)

    now = datetime.now(UTC)
    record_id = str(ULID())
    record = models.ParentConsentRecord(
        id=record_id,
        parent_email=normalized_email,
        kid_display_name=payload.kid_display_name,
        policy_version=version,
        # `consent_text_version` is intentionally not collected from the
        # client today -- the policy_version + the published copy at
        # /privacy together pin the text the parent saw. If we ever
        # split policy meta-version from displayed text version we'll
        # populate this.
        source="web_consent",
        recorded_at=now,
        browser_nonce_sha256=nonce_hash,
    )
    session.add(record)
    try:
        await session.flush()
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        if _integrity_constraint_name(exc) != "uq_parent_consent_browser_nonce_sha256":
            raise
        winner = await _load_consent_by_nonce_hash(session, nonce_hash)
        if winner is None:
            raise
        if not _consent_replay_matches(
            winner,
            normalized_email=normalized_email,
        ):
            _raise_consent_replay_conflict()
        response.headers["Idempotency-Replayed"] = "true"
        return _consent_response(winner)

    log.info(
        "auth.consent.recorded",
        consent_id=record_id,
        policy_version=version,
        recorded_at=now.isoformat(),
    )
    return _consent_response(record)
