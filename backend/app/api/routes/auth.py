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
    InvalidDragonflyJwt,
    mint_session_token,
    public_jwks,
    verify_dragonfly_jwt,
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
_CURRENT_POLICY_VERSION = "2026-05-10-DRAFT"


class ParentSignupRequest(BaseModel):
    display_name: str = Field(..., min_length=1, max_length=80)


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
        dragonfly_sub=user.id if user.role == "kid" else None,
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

    if entra_oid is not None:
        result = await session.execute(
            select(models.User).where(models.User.entra_oid == entra_oid)
        )
    else:
        # Back-compat: legacy stub-token path.
        result = await session.execute(
            select(models.User).where(models.User.firebase_uid == current_user.uid)
        )
    existing = result.scalar_one_or_none()

    if existing is not None:
        # Backfill entra_oid on legacy rows whose first sign-in is via Entra.
        if entra_oid is not None and getattr(existing, "entra_oid", None) is None:
            existing.entra_oid = entra_oid
            await session.commit()
        log.info(
            "auth.parent_signup.idempotent",
            user_id=existing.id,
            entra_oid=entra_oid,
            firebase_uid=existing.firebase_uid,
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
    session.add(new_user)
    await session.commit()
    await session.refresh(new_user)

    # Best-effort: thread the audit trail by linking the most recent
    # unlinked consent record for this verified email back to the new
    # parent row. Failure here must NOT break signup -- consent rows
    # remain durable in their own table even if this UPDATE no-ops.
    # TODO(PR-4): require a consent record at signup time once the
    # client passes the consent_id back explicitly.
    linked_consent_id: str | None = None
    if current_user.email:
        linked_consent_id = await _link_latest_consent_to_parent(
            session,
            parent_email=current_user.email,
            parent_user_id=new_user.id,
        )

    log.info(
        "auth.parent_signup.created",
        user_id=new_user.id,
        entra_oid=entra_oid,
        linked_consent_id=linked_consent_id,
    )
    return UserResponse.from_model(new_user)


async def _link_latest_consent_to_parent(
    session: AsyncSession,
    *,
    parent_email: str,
    parent_user_id: str,
) -> str | None:
    """Stamp the newest unlinked consent row for ``parent_email`` with
    ``parent_user_id``. Returns the consent row id, or ``None`` if no
    matching unlinked row exists.

    The "newest unlinked" rule keeps the join stable when a parent
    re-consents (e.g. after a policy bump): an already-linked older row
    stays put; the fresh one threads through. Email matching is
    case-insensitive because Entra normalises but historical identity rows may
    not.
    """
    stmt = (
        select(models.ParentConsentRecord)
        .where(
            models.ParentConsentRecord.linked_parent_user_id.is_(None),
        )
        .order_by(models.ParentConsentRecord.recorded_at.desc())
    )
    result = await session.execute(stmt)
    for row in result.scalars():
        if row.parent_email.lower() == parent_email.lower():
            row.linked_parent_user_id = parent_user_id
            await session.commit()
            return row.id
    return None


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
        claims = verify_dragonfly_jwt(
            payload.handoff_token,
            settings=settings,
            expected_token_type="handoff",
        )
    except InvalidDragonflyJwt as exc:
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
        seconds=settings.dragonfly_session_ttl_seconds
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
        seconds=settings.dragonfly_session_ttl_seconds
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
# GET public JWKS for kid tokens (additive rebrand aliases)
# ---------------------------------------------------------------------------


@well_known_router.get(
    "/.well-known/hinterland-kid-jwks.json",
    include_in_schema=False,
)
@well_known_router.get(
    "/.well-known/dragonfly-kid-jwks.json",
    include_in_schema=False,
)
def kid_jwks(
    response: Response,
    settings: Annotated[Settings, Depends(get_request_settings)],
) -> dict[str, object]:
    """Return the Hinterland kid-JWT signing key in JWKS format.

    Used by any downstream service (mobile app, future services) to verify
    Hinterland-minted kid handoff / session tokens. The same kid is rotated
    rarely (manifest constant `dragonfly_jwt_kid`), so this response is
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

    When the parent later signs up via `parent_signup`, that flow
    links back to the newest record matching the verified email so
    the audit ledger threads parent -> consent -> users.id.
    """

    # Lightweight regex check -- avoids pulling in email-validator just
    # for one endpoint. Real semantic validation happens via Entra at
    # signup time.
    email: str = Field(..., pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", max_length=320)
    kid_display_name: str | None = Field(default=None, max_length=80)
    policy_version: str | None = None


class ConsentResponse(BaseModel):
    id: str
    recorded_at: datetime
    policy_version: str


@router.post(
    "/auth/consent",
    response_model=ConsentResponse,
    status_code=status.HTTP_200_OK,
)
async def record_consent(
    payload: ConsentRequest,
    session: DbSessionDep,
) -> ConsentResponse:
    """Record COPPA parental consent. Public, unauthenticated.

    Storage shape today:
      1. INSERTs a `parent_consent_records` row -- the durable audit
         ledger. Indexed by email + policy_version + recorded_at so
         the parent-signup flow can locate the newest matching record.
      2. Emits `auth.consent.recorded` to structured logs with the
         new row id so the existing log-based ops dashboards keep
         working.

    The response carries the row id so the frontend can pass it back
    on parent_signup (a future improvement; today the join is via
    email + recency).
    """
    version = payload.policy_version or _CURRENT_POLICY_VERSION
    now = datetime.now(UTC)
    record_id = str(ULID())
    record = models.ParentConsentRecord(
        id=record_id,
        parent_email=payload.email,
        kid_display_name=payload.kid_display_name,
        policy_version=version,
        # `consent_text_version` is intentionally not collected from the
        # client today -- the policy_version + the published copy at
        # /privacy together pin the text the parent saw. If we ever
        # split policy meta-version from displayed text version we'll
        # populate this.
        source="web_consent",
        recorded_at=now,
    )
    session.add(record)
    await session.commit()

    log.info(
        "auth.consent.recorded",
        consent_id=record_id,
        email=payload.email,
        kid_display_name=payload.kid_display_name,
        policy_version=version,
        recorded_at=now.isoformat(),
    )
    return ConsentResponse(id=record_id, recorded_at=now, policy_version=version)
