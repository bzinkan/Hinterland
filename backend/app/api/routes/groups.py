"""Group create + kid-provisioning + join routes."""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

import structlog
from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, or_, select, update
from ulid import ULID

from app.core.auth import CurrentUserDep, bust_user_cache, resolve_current_user_row
from app.core.config import Settings, get_request_settings
from app.core.kid_jwt import mint_handoff_token
from app.core.parent_consent import (
    CURRENT_PARENT_CONSENT_REQUIRED_MESSAGE,
    CurrentParentConsentRequiredError,
    require_linked_current_parent_consent,
)
from app.db import models
from app.db.session import DbSessionDep

AgeBand = Literal["9-10", "11-12", "13+"]

router = APIRouter(prefix="/v1", tags=["groups"])

log = structlog.get_logger()

# Crockford base32: A-Z + 0-9 minus the visually ambiguous I, L, O, U.
# 32 chars, 6 positions = ~1B codes. Generous against collisions for the
# closed-beta scale.
_JOIN_CODE_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_JOIN_CODE_LENGTH = 6
_MAX_JOIN_CODE_ATTEMPTS = 5

_ADULT_ROLES = frozenset({"parent", "teacher"})
_GROUP_CREATOR_ROLES = frozenset({"parent"})
_INVITE_TTL = timedelta(hours=72)


def generate_join_code() -> str:
    """Generate a 6-char Crockford-base32 join code using a CSPRNG."""
    return "".join(secrets.choice(_JOIN_CODE_ALPHABET) for _ in range(_JOIN_CODE_LENGTH))


class GroupCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("group name cannot be blank")
        return normalized


class GroupPermissions(BaseModel):
    can_rename: bool
    can_archive: bool
    can_invite_parents: bool
    can_manage_invitations: bool
    can_remove_adults: bool
    can_add_child: bool


class GroupResponse(BaseModel):
    """Adult-safe group summary; raw join and owner IDs are never exposed."""

    id: str
    name: str
    is_owner: bool
    adult_count: int
    child_count: int
    own_children_count: int
    permissions: GroupPermissions


def _group_permissions(
    group: models.Group,
    caller: models.User,
    settings: Settings,
) -> GroupPermissions:
    is_owner = group.owner_user_id == caller.id
    is_parent = caller.role == "parent"
    shared_groups_enabled = settings.shared_groups_allowed_for(group.id)
    shared_groups_started = group.shared_groups_enabled_at is not None
    return GroupPermissions(
        can_rename=is_owner,
        can_archive=is_owner,
        # Creation/redemption stay fail-closed on flag rollback. Safety
        # controls for already-started shared groups remain available so an
        # owner can revoke pending links and remove adults.
        can_invite_parents=is_owner and shared_groups_enabled,
        can_manage_invitations=is_owner and (shared_groups_enabled or shared_groups_started),
        can_remove_adults=is_owner and (shared_groups_enabled or shared_groups_started),
        can_add_child=is_parent and (is_owner or shared_groups_enabled),
    )


async def _group_response(
    session: DbSessionDep,
    *,
    group: models.Group,
    caller: models.User,
    settings: Settings,
) -> GroupResponse:
    counts = (
        await session.execute(
            select(
                func.count(models.Membership.id)
                .filter(models.Membership.role.in_(_ADULT_ROLES))
                .label("adult_count"),
                func.count(models.Membership.id)
                .filter(models.Membership.role == "kid")
                .label("child_count"),
                func.count(models.Membership.id)
                .filter(
                    models.Membership.role == "kid",
                    models.User.parent_user_id == caller.id,
                )
                .label("own_children_count"),
            )
            .join(models.User, models.User.id == models.Membership.user_id)
            .where(
                models.Membership.group_id == group.id,
                models.Membership.status == "active",
            )
        )
    ).one()
    return GroupResponse(
        id=group.id,
        name=group.name,
        is_owner=group.owner_user_id == caller.id,
        adult_count=int(counts.adult_count or 0),
        child_count=int(counts.child_count or 0),
        own_children_count=int(counts.own_children_count or 0),
        permissions=_group_permissions(group, caller, settings),
    )


async def _require_active_adult_membership(
    session: DbSessionDep,
    *,
    group_id: str,
    user_id: str,
) -> models.Membership:
    membership = (
        await session.execute(
            select(models.Membership).where(
                models.Membership.group_id == group_id,
                models.Membership.user_id == user_id,
                models.Membership.role.in_(_ADULT_ROLES),
                models.Membership.status == "active",
            )
        )
    ).scalar_one_or_none()
    if membership is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Group not found",
        )
    return membership


async def _load_owned_group(
    session: DbSessionDep,
    *,
    group_id: str,
    owner_user_id: str,
    include_archived: bool = False,
) -> models.Group:
    clauses = [
        models.Group.id == group_id,
        models.Group.owner_user_id == owner_user_id,
    ]
    if not include_archived:
        clauses.append(models.Group.archived_at.is_(None))
    group = (
        await session.execute(select(models.Group).where(*clauses).with_for_update())
    ).scalar_one_or_none()
    if group is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
    return group


def _require_shared_groups(settings: Settings, group_id: str) -> None:
    if not settings.shared_groups_allowed_for(group_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")


def _require_shared_group_safety_controls(
    settings: Settings,
    group: models.Group,
) -> None:
    """Keep containment controls live after a shared-groups flag rollback."""
    if not settings.shared_groups_allowed_for(group.id) and group.shared_groups_enabled_at is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")


@router.post(
    "/groups",
    response_model=GroupResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_group(
    request_body: GroupCreateRequest,
    current_user: CurrentUserDep,
    session: DbSessionDep,
    settings: Annotated[Settings, Depends(get_request_settings)],
) -> GroupResponse:
    """Create a group owned by the calling parent.

    Authorization is gated on the canonical `users.role` from Postgres rather
    than a cached token claim, so a parent who just signed up can create a
    group without waiting for a token refresh. The parent must also have a
    receipt linked for the exact active consent policy. Compatibility-only
    roles cannot create groups.

    A legacy join code is still allocated for schema compatibility, but it is
    never returned by the Groups DTO. Shared groups use one-time adult
    invitations instead.

    Idempotency is **not** offered here -- a parent who calls `POST /v1/groups`
    twice creates two groups. Phase 1's family flow creates exactly one group
    per parent, so the client should gate the call.
    """
    user = await resolve_current_user_row(
        session,
        current_user,
        missing_user_status=status.HTTP_404_NOT_FOUND,
    )

    if user.role not in _GROUP_CREATOR_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Role '{user.role}' cannot create groups.",
        )

    try:
        await require_linked_current_parent_consent(
            session,
            parent_user_id=user.id,
        )
    except CurrentParentConsentRequiredError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=CURRENT_PARENT_CONSENT_REQUIRED_MESSAGE,
        ) from exc

    join_code: str | None = None
    for _ in range(_MAX_JOIN_CODE_ATTEMPTS):
        candidate = generate_join_code()
        existing_code = await session.execute(
            select(models.Group.id).where(models.Group.join_code == candidate)
        )
        if existing_code.scalar_one_or_none() is None:
            join_code = candidate
            break

    if join_code is None:
        log.error("groups.create.join_code_exhausted", attempts=_MAX_JOIN_CODE_ATTEMPTS)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Could not allocate a unique join code; please retry.",
        )

    group = models.Group(
        id=str(ULID()),
        name=request_body.name,
        join_code=join_code,
        owner_user_id=user.id,
    )
    membership = models.Membership(
        id=str(ULID()),
        group_id=group.id,
        user_id=user.id,
        role=user.role,
    )

    session.add(group)
    session.add(membership)
    await session.commit()
    await session.refresh(group)

    log.info("groups.create", result="created", owner_role=user.role)
    return GroupResponse(
        id=group.id,
        name=group.name,
        is_owner=True,
        adult_count=1,
        child_count=0,
        own_children_count=0,
        permissions=_group_permissions(group, user, settings),
    )


# ---------------------------------------------------------------------------
# GET /v1/groups -- list groups the caller belongs to
# ---------------------------------------------------------------------------


class GroupListResponse(BaseModel):
    items: list[GroupResponse]


@router.get("/groups", response_model=GroupListResponse)
async def list_groups(
    current_user: CurrentUserDep,
    session: DbSessionDep,
    settings: Annotated[Settings, Depends(get_request_settings)],
    response: Response,
) -> GroupListResponse:
    """List every non-archived group the caller has a membership in.

    Used by the adult group picker. Both owned and joined groups appear, with
    explicit privacy-safe permissions rather than raw owner identifiers.

    Order is newest-group-first so a freshly-created group is at the top.
    """
    user = await resolve_current_user_row(
        session,
        current_user,
        allowed_roles=_ADULT_ROLES,
        missing_user_status=status.HTTP_404_NOT_FOUND,
    )

    result = await session.execute(
        select(models.Group)
        .join(models.Membership, models.Membership.group_id == models.Group.id)
        .where(
            models.Membership.user_id == user.id,
            models.Membership.status == "active",
            models.Group.archived_at.is_(None),
        )
        .order_by(models.Group.created_at.desc())
    )
    groups = result.scalars().all()
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Pragma"] = "no-cache"
    return GroupListResponse(
        items=[
            await _group_response(session, group=group, caller=user, settings=settings)
            for group in groups
        ]
    )


# ---------------------------------------------------------------------------
# GET /v1/groups/owned-children -- caller-owned child placement inventory
# ---------------------------------------------------------------------------


class OwnedChildSummary(BaseModel):
    """Minimal parent-only child record used to recover group placement."""

    id: str
    display_name: str
    age_band: str | None
    active_group_id: str | None


class OwnedChildrenResponse(BaseModel):
    items: list[OwnedChildSummary]


@router.get("/groups/owned-children", response_model=OwnedChildrenResponse)
async def list_owned_children(
    current_user: CurrentUserDep,
    session: DbSessionDep,
    response: Response,
) -> OwnedChildrenResponse:
    """List only the caller's children and their current active group.

    A child remains visible here after an owner removes the child's parent
    from a group. The parent can therefore place that same child into another
    active group without exposing peer children, membership identifiers, or
    derived progress. The active-group value is derived exclusively from an
    active kid membership; historical/left memberships intentionally produce
    ``null``.
    """
    parent = await resolve_current_user_row(
        session,
        current_user,
        allowed_roles={"parent"},
        missing_user_status=status.HTTP_404_NOT_FOUND,
    )
    result = await session.execute(
        select(models.User, models.Membership.group_id)
        .outerjoin(
            models.Membership,
            (models.Membership.user_id == models.User.id)
            & (models.Membership.role == "kid")
            & (models.Membership.status == "active"),
        )
        .where(
            models.User.role == "kid",
            models.User.parent_user_id == parent.id,
            models.User.disabled_at.is_(None),
        )
        .order_by(func.lower(models.User.display_name), models.User.id)
    )
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Pragma"] = "no-cache"
    return OwnedChildrenResponse(
        items=[
            OwnedChildSummary(
                id=child.id,
                display_name=child.display_name,
                age_band=child.age_band,
                active_group_id=active_group_id,
            )
            for child, active_group_id in result.all()
        ]
    )


# ---------------------------------------------------------------------------
# GET /v1/groups/{group_id}/members -- minimized adult group roster
# ---------------------------------------------------------------------------


class AdultRosterMember(BaseModel):
    removal_ref: str | None = None
    display_name: str
    is_owner: bool
    status: str


class OwnChildRosterMember(BaseModel):
    user_id: str
    display_name: str
    age_band: str | None
    status: str
    observation_count: int
    dex_count: int
    rarest_tier: str | None
    last_observed_at: datetime | None


class RosterResponse(BaseModel):
    group: GroupResponse
    adults: list[AdultRosterMember]
    own_children: list[OwnChildRosterMember]
    other_child_count: int


@router.get(
    "/groups/{group_id}/members",
    response_model=RosterResponse,
)
async def list_group_members(
    group_id: str,
    current_user: CurrentUserDep,
    session: DbSessionDep,
    settings: Annotated[Settings, Depends(get_request_settings)],
    response: Response,
) -> RosterResponse:
    """Return the minimum adult-management roster for one active group.

    Owners can administer adult memberships. Every parent sees complete rows
    only for their own children; other families are an aggregate count.
    """
    caller = await resolve_current_user_row(
        session,
        current_user,
        allowed_roles=_ADULT_ROLES,
        missing_user_status=status.HTTP_404_NOT_FOUND,
    )

    group_result = await session.execute(
        select(models.Group).where(
            models.Group.id == group_id,
            models.Group.archived_at.is_(None),
        )
    )
    group = group_result.scalar_one_or_none()
    if group is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Group '{group_id}' not found.",
        )

    await _require_active_adult_membership(
        session,
        group_id=group.id,
        user_id=caller.id,
    )
    members_result = await session.execute(
        select(models.Membership, models.User)
        .join(models.User, models.User.id == models.Membership.user_id)
        .where(
            models.Membership.group_id == group.id,
            # Owners retain a minimized audit/repair view of adults who have
            # left. Child rows remain active-only; historical child
            # memberships never reappear in the roster.
            or_(
                models.Membership.role.in_(_ADULT_ROLES),
                models.Membership.status == "active",
            ),
        )
    )
    # Materialize rows as plain tuples up front so the sort key has a
    # stable, mypy-friendly type (Result.all() returns Row[tuple[...]],
    # which sorted() doesn't accept directly).
    rows: list[tuple[models.Membership, models.User]] = [(m, u) for m, u in members_result.all()]

    is_owner = group.owner_user_id == caller.id
    adult_rows = sorted(
        ((m, u) for m, u in rows if m.role in _ADULT_ROLES),
        key=lambda row: row[1].display_name.lower(),
    )
    if not is_owner:
        adult_rows = [(m, u) for m, u in adult_rows if u.id == caller.id]
    adults = [
        AdultRosterMember(
            removal_ref=m.management_ref if is_owner and u.id != group.owner_user_id else None,
            display_name=u.display_name,
            is_owner=u.id == group.owner_user_id,
            status=m.status,
        )
        for m, u in adult_rows
    ]
    own_child_rows = sorted(
        (
            (m, u)
            for m, u in rows
            if m.role == "kid" and m.status == "active" and u.parent_user_id == caller.id
        ),
        key=lambda row: row[1].display_name.lower(),
    )
    own_children = [
        OwnChildRosterMember(
            user_id=u.id,
            display_name=u.display_name,
            age_band=u.age_band,
            status=m.status,
            observation_count=m.observation_count,
            dex_count=m.dex_count,
            rarest_tier=m.rarest_tier,
            last_observed_at=m.last_observed_at,
        )
        for m, u in own_child_rows
    ]
    other_child_count = sum(
        1
        for m, u in rows
        if m.role == "kid" and m.status == "active" and u.parent_user_id != caller.id
    )
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Pragma"] = "no-cache"
    return RosterResponse(
        group=await _group_response(
            session,
            group=group,
            caller=caller,
            settings=settings,
        ),
        adults=adults,
        own_children=own_children,
        other_child_count=other_child_count,
    )


# ---------------------------------------------------------------------------
# POST /v1/groups/{group_id}/kids -- create a local kid and handoff JWT
# ---------------------------------------------------------------------------


_KID_PROVISIONER_ROLES = frozenset({"parent"})


class KidCreateRequest(BaseModel):
    display_name: str = Field(..., min_length=1, max_length=80)
    age_band: AgeBand


class KidHandoffResponse(BaseModel):
    """Minimal public shape of a kid plus a fresh handoff JWT.

    The `handoff_token` is a single-use Hinterland-signed RS256 JWT (typ
    `handoff`, 15-minute TTL). The parent hands it to the kid's device via
    QR code / NFC; the kid's app POSTs it to `/v1/auth/kid-exchange` to
    swap it for a long-lived session JWT. The handoff JWT's `jti` is
    consumed atomically on first exchange.
    """

    id: str
    display_name: str
    age_band: str
    handoff_token: str
    expires_at: datetime


class KidCreateResponse(KidHandoffResponse):
    """One-release-compatible kid-create response."""

    firebase_uid: str | None = None


@router.post(
    "/groups/{group_id}/kids",
    response_model=KidCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_kid(
    group_id: str,
    request_body: KidCreateRequest,
    current_user: CurrentUserDep,
    session: DbSessionDep,
    settings: Annotated[Settings, Depends(get_request_settings)],
    response: Response,
) -> KidCreateResponse:
    """Admin-create a kid account inside a group and return a handoff JWT.

    Authorization: the caller must be a parent with a receipt linked for the
    exact active parental-consent policy and an active adult membership in the
    target group. A joined parent may create only their own child after shared
    groups are enabled for that canary group.

    Side effects (in order):
    1. Insert `users` row (firebase_uid=NULL, entra_oid=NULL; kids have no
       external IdP identity).
    2. Insert `memberships` row binding the kid to the group.
    3. Mint a Hinterland-signed RS256 handoff JWT (15-minute TTL, single-use)
       embedding `sub=kid_id`, `group_id`, `parent_id`, `token_use=handoff`.

    The kid's device receives the handoff JWT (via QR/NFC from the parent),
    then POSTs it to `/v1/auth/kid-exchange` for a long-lived session JWT.
    Single-use is enforced by an atomic INSERT into `kid_handoff_jti` at
    redemption time -- no orphan-cleanup logic needed here.
    """
    caller = await resolve_current_user_row(
        session,
        current_user,
        missing_user_status=status.HTTP_404_NOT_FOUND,
    )
    if caller.role not in _KID_PROVISIONER_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Role '{caller.role}' cannot provision kids.",
        )

    try:
        await require_linked_current_parent_consent(
            session,
            parent_user_id=caller.id,
        )
    except CurrentParentConsentRequiredError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=CURRENT_PARENT_CONSENT_REQUIRED_MESSAGE,
        ) from exc

    group_result = await session.execute(
        select(models.Group)
        .where(
            models.Group.id == group_id,
            models.Group.archived_at.is_(None),
        )
        .with_for_update()
    )
    group = group_result.scalar_one_or_none()
    if group is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Group '{group_id}' not found.",
        )
    await _require_active_adult_membership(
        session,
        group_id=group.id,
        user_id=caller.id,
    )
    if group.owner_user_id != caller.id:
        _require_shared_groups(settings, group.id)

    # Kids have no external IdP identity: no legacy uid, no Entra OID.
    # The local users.id (ULID) IS their identity for token `sub` claims.
    kid_id = str(ULID())
    kid = models.User(
        id=kid_id,
        firebase_uid=None,
        role="kid",
        display_name=request_body.display_name,
        age_band=request_body.age_band,
        parent_user_id=caller.id,
    )
    membership = models.Membership(
        id=str(ULID()),
        group_id=group.id,
        user_id=kid.id,
        role="kid",
        session_version=1,
    )
    # Flush the kid User insert before adding the Membership so the FK
    # target exists by the time the Membership INSERT runs. SQLAlchemy's
    # topological sort gets confused by the self-referential User
    # parent_user_id FK in the same flush as a Membership FK to users.id;
    # the explicit flush forces the right order.
    session.add(kid)
    await session.flush()
    session.add(membership)
    await session.commit()
    await session.refresh(kid)

    # Mint the handoff JWT only after the kid + membership are durably
    # committed. If anything above raises, the SQLAlchemy session rolls
    # back and no token is ever produced -- no orphan to clean up.
    handoff_token, _jti = mint_handoff_token(
        kid_user_id=kid.id,
        parent_id=caller.id,
        group_id=group.id,
        session_version=membership.session_version,
        settings=settings,
    )
    handoff_expires_at = datetime.now(UTC) + timedelta(
        seconds=settings.hinterland_handoff_ttl_seconds
    )

    log.info("groups.create_kid", result="created")
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Pragma"] = "no-cache"

    return KidCreateResponse(
        id=kid.id,
        firebase_uid=None,
        display_name=kid.display_name,
        age_band=str(kid.age_band),
        handoff_token=handoff_token,
        expires_at=handoff_expires_at,
    )


# ---------------------------------------------------------------------------
# POST /v1/groups/{group_id}/kids/{kid_user_id}/handoff -- reissue kid QR
# ---------------------------------------------------------------------------


@router.post(
    "/groups/{group_id}/kids/{kid_user_id}/handoff",
    response_model=KidHandoffResponse,
    status_code=status.HTTP_200_OK,
)
async def reissue_kid_handoff(
    group_id: str,
    kid_user_id: str,
    current_user: CurrentUserDep,
    session: DbSessionDep,
    settings: Annotated[Settings, Depends(get_request_settings)],
    response: Response,
) -> KidHandoffResponse:
    """Mint a fresh, short-lived handoff for an existing kid account.

    This is the recovery path for a new device, an expired QR, or an
    owner-scoped offline queue preserved across sign-out. Only the parent who
    is the child's canonical parent and remains active in the child's group
    may mint another token.
    The token is returned once, is never persisted by this endpoint, and is
    consumed atomically by ``POST /v1/auth/kid-exchange``.

    Previously minted, unconsumed handoffs remain independently single-use
    until their 15-minute expiry. The UI therefore describes this as a new QR,
    not as invalidating an earlier QR or another device's active session.
    """
    caller = await resolve_current_user_row(
        session,
        current_user,
        missing_user_status=status.HTTP_404_NOT_FOUND,
    )
    if caller.role not in _KID_PROVISIONER_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Role '{caller.role}' cannot provision kid handoffs.",
        )
    try:
        await require_linked_current_parent_consent(
            session,
            parent_user_id=caller.id,
        )
    except CurrentParentConsentRequiredError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=CURRENT_PARENT_CONSENT_REQUIRED_MESSAGE,
        ) from exc

    group_result = await session.execute(
        select(models.Group).where(
            models.Group.id == group_id,
            models.Group.archived_at.is_(None),
        )
    )
    group = group_result.scalar_one_or_none()
    if group is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Group '{group_id}' not found.",
        )
    await _require_active_adult_membership(
        session,
        group_id=group.id,
        user_id=caller.id,
    )

    kid_result = await session.execute(
        select(models.User, models.Membership)
        .join(models.Membership, models.Membership.user_id == models.User.id)
        .where(
            models.User.id == kid_user_id,
            models.User.role == "kid",
            models.User.parent_user_id == caller.id,
            models.User.disabled_at.is_(None),
            models.Membership.group_id == group.id,
            models.Membership.role == "kid",
            models.Membership.status == "active",
        )
    )
    kid_row = kid_result.one_or_none()
    if kid_row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Kid not found in this group.",
        )

    kid, membership = kid_row
    handoff_token, _jti = mint_handoff_token(
        kid_user_id=kid.id,
        parent_id=caller.id,
        group_id=group.id,
        session_version=membership.session_version,
        settings=settings,
    )
    handoff_expires_at = datetime.now(UTC) + timedelta(
        seconds=settings.hinterland_handoff_ttl_seconds
    )
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Pragma"] = "no-cache"

    log.info("groups.reissue_kid_handoff", result="issued")

    return KidHandoffResponse(
        id=kid.id,
        display_name=kid.display_name,
        age_band=str(kid.age_band),
        handoff_token=handoff_token,
        expires_at=handoff_expires_at,
    )


@router.post(
    "/groups/{group_id}/kids/{kid_user_id}/membership",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def place_existing_child_in_group(
    group_id: str,
    kid_user_id: str,
    current_user: CurrentUserDep,
    session: DbSessionDep,
    settings: Annotated[Settings, Depends(get_request_settings)],
) -> Response:
    """Place the caller's own child after the child's previous group was left."""
    parent = await resolve_current_user_row(
        session,
        current_user,
        allowed_roles={"parent"},
        missing_user_status=status.HTTP_404_NOT_FOUND,
    )
    try:
        await require_linked_current_parent_consent(session, parent_user_id=parent.id)
    except CurrentParentConsentRequiredError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=CURRENT_PARENT_CONSENT_REQUIRED_MESSAGE,
        ) from exc
    group = (
        await session.execute(
            select(models.Group)
            .where(
                models.Group.id == group_id,
                models.Group.archived_at.is_(None),
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if group is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
    await _require_active_adult_membership(
        session,
        group_id=group.id,
        user_id=parent.id,
    )
    if group.owner_user_id != parent.id:
        _require_shared_groups(settings, group.id)
    kid = (
        await session.execute(
            select(models.User)
            .where(
                models.User.id == kid_user_id,
                models.User.role == "kid",
                models.User.parent_user_id == parent.id,
                models.User.disabled_at.is_(None),
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if kid is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Child not found")

    memberships = (
        (
            await session.execute(
                select(models.Membership).where(
                    models.Membership.user_id == kid.id,
                    models.Membership.role == "kid",
                )
            )
        )
        .scalars()
        .all()
    )
    active = next((m for m in memberships if m.status == "active"), None)
    if active is not None and active.group_id != group.id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Child already belongs to another active group.",
        )
    if active is None:
        existing = next((m for m in memberships if m.group_id == group.id), None)
        if existing is None:
            session.add(
                models.Membership(
                    id=str(ULID()),
                    group_id=group.id,
                    user_id=kid.id,
                    role="kid",
                    status="active",
                    session_version=1,
                )
            )
        else:
            existing.status = "active"
            existing.left_at = None
            existing.session_version += 1
        await session.commit()
        bust_user_cache(kid.id, hinterland_sub=kid.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Group ownership and privacy-safe adult invitations
# ---------------------------------------------------------------------------


class MembershipResponse(BaseModel):
    id: str
    group_id: str
    user_id: str
    role: str

    @classmethod
    def from_model(cls, membership: models.Membership) -> MembershipResponse:
        return cls(
            id=membership.id,
            group_id=membership.group_id,
            user_id=membership.user_id,
            role=membership.role,
        )


class GroupUpdateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("group name cannot be blank")
        return normalized


@router.patch("/groups/{group_id}", response_model=GroupResponse)
async def update_group(
    group_id: str,
    request_body: GroupUpdateRequest,
    current_user: CurrentUserDep,
    session: DbSessionDep,
    settings: Annotated[Settings, Depends(get_request_settings)],
) -> GroupResponse:
    owner = await resolve_current_user_row(
        session,
        current_user,
        allowed_roles=_ADULT_ROLES,
        missing_user_status=status.HTTP_404_NOT_FOUND,
    )
    group = await _load_owned_group(
        session,
        group_id=group_id,
        owner_user_id=owner.id,
    )
    group.name = request_body.name.strip()
    await session.commit()
    await session.refresh(group)
    return await _group_response(session, group=group, caller=owner, settings=settings)


@router.post("/groups/{group_id}/archive", status_code=status.HTTP_204_NO_CONTENT)
async def archive_group(
    group_id: str,
    current_user: CurrentUserDep,
    session: DbSessionDep,
) -> Response:
    owner = await resolve_current_user_row(
        session,
        current_user,
        allowed_roles=_ADULT_ROLES,
        missing_user_status=status.HTTP_404_NOT_FOUND,
    )
    group = await _load_owned_group(
        session,
        group_id=group_id,
        owner_user_id=owner.id,
        include_archived=True,
    )
    if group.archived_at is None:
        now = datetime.now(UTC)
        group.archived_at = now
        affected_users = (
            (
                await session.execute(
                    select(models.User)
                    .join(
                        models.Membership,
                        models.Membership.user_id == models.User.id,
                    )
                    .where(
                        models.Membership.group_id == group.id,
                        models.Membership.status == "active",
                    )
                )
            )
            .scalars()
            .all()
        )
        await session.execute(
            update(models.Membership)
            .where(
                models.Membership.group_id == group.id,
                models.Membership.status == "active",
            )
            .values(
                status="left",
                left_at=now,
                session_version=models.Membership.session_version + 1,
            )
        )
        await session.execute(
            update(models.GroupAdultInvite)
            .where(
                models.GroupAdultInvite.group_id == group.id,
                models.GroupAdultInvite.redeemed_at.is_(None),
                models.GroupAdultInvite.revoked_at.is_(None),
            )
            .values(revoked_at=now, revoked_by_user_id=owner.id)
        )
        await session.commit()
        for affected_user in affected_users:
            bust_user_cache(
                affected_user.id,
                entra_oid=affected_user.entra_oid,
                legacy_uid=affected_user.firebase_uid,
                hinterland_sub=(affected_user.id if affected_user.role == "kid" else None),
            )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete(
    "/groups/{group_id}/adult-members/{removal_ref}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_adult_member(
    group_id: str,
    removal_ref: str,
    current_user: CurrentUserDep,
    session: DbSessionDep,
    settings: Annotated[Settings, Depends(get_request_settings)],
) -> Response:
    owner = await resolve_current_user_row(
        session,
        current_user,
        allowed_roles=_ADULT_ROLES,
        missing_user_status=status.HTTP_404_NOT_FOUND,
    )
    group = await _load_owned_group(
        session,
        group_id=group_id,
        owner_user_id=owner.id,
    )
    _require_shared_group_safety_controls(settings, group)
    row = (
        await session.execute(
            select(models.Membership, models.User)
            .join(models.User, models.User.id == models.Membership.user_id)
            .where(
                models.Membership.management_ref == removal_ref,
                models.Membership.group_id == group.id,
                models.Membership.role.in_(_ADULT_ROLES),
            )
            .with_for_update()
        )
    ).one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Member not found")
    membership, adult = row
    if adult.id == group.owner_user_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The group owner cannot be removed.",
        )
    # Always sweep the canonical parent's active children, even when the
    # adult membership is already left. This makes repeated removal a
    # fail-safe repair operation for any child row that survived an earlier
    # partial/raced transition.
    children = (
        await session.execute(
            select(models.Membership, models.User)
            .join(models.User, models.User.id == models.Membership.user_id)
            .where(
                models.Membership.group_id == group.id,
                models.Membership.role == "kid",
                models.Membership.status == "active",
                models.User.parent_user_id == adult.id,
            )
            .with_for_update()
        )
    ).all()
    if membership.status == "active" or children:
        now = datetime.now(UTC)
        if membership.status == "active":
            membership.status = "left"
            membership.left_at = now
        for child_membership, _child in children:
            child_membership.status = "left"
            child_membership.left_at = now
            child_membership.session_version += 1
        await session.commit()
        bust_user_cache(
            adult.id,
            entra_oid=adult.entra_oid,
            legacy_uid=adult.firebase_uid,
        )
        for _child_membership, child in children:
            bust_user_cache(child.id, hinterland_sub=child.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


InviteState = Literal["pending", "redeemed", "revoked", "expired"]


def _invite_state(invite: models.GroupAdultInvite, now: datetime) -> InviteState:
    if invite.redeemed_at is not None:
        return "redeemed"
    if invite.revoked_at is not None:
        return "revoked"
    if invite.expires_at <= now:
        return "expired"
    return "pending"


class AdultInviteResponse(BaseModel):
    id: str
    state: InviteState
    created_at: datetime
    expires_at: datetime
    redeemed_at: datetime | None = None
    revoked_at: datetime | None = None


class AdultInviteCreateResponse(AdultInviteResponse):
    invite_url: str


class AdultInviteListResponse(BaseModel):
    items: list[AdultInviteResponse]


class AdultInviteRedeemRequest(BaseModel):
    # Validate in the route so FastAPI never echoes this bearer-like secret in
    # a Pydantic validation error's `input` field.
    token: str


class AdultInviteRedeemResponse(BaseModel):
    group_id: str
    joined: bool = True
    replayed: bool


def _invite_response(invite: models.GroupAdultInvite, now: datetime) -> AdultInviteResponse:
    return AdultInviteResponse(
        id=invite.id,
        state=_invite_state(invite, now),
        created_at=invite.created_at,
        expires_at=invite.expires_at,
        redeemed_at=invite.redeemed_at,
        revoked_at=invite.revoked_at,
    )


@router.post(
    "/groups/{group_id}/adult-invitations",
    response_model=AdultInviteCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_adult_invitation(
    group_id: str,
    current_user: CurrentUserDep,
    session: DbSessionDep,
    settings: Annotated[Settings, Depends(get_request_settings)],
    response: Response,
) -> AdultInviteCreateResponse:
    owner = await resolve_current_user_row(
        session,
        current_user,
        allowed_roles=_ADULT_ROLES,
        missing_user_status=status.HTTP_404_NOT_FOUND,
    )
    group = await _load_owned_group(
        session,
        group_id=group_id,
        owner_user_id=owner.id,
    )
    _require_shared_groups(settings, group.id)

    token = secrets.token_urlsafe(32)
    token_sha256 = hashlib.sha256(token.encode("ascii")).hexdigest()
    now = datetime.now(UTC)
    if group.shared_groups_enabled_at is None:
        group.shared_groups_enabled_at = now
    invite = models.GroupAdultInvite(
        id=str(ULID()),
        group_id=group.id,
        created_by_user_id=owner.id,
        token_sha256=token_sha256,
        expires_at=now + _INVITE_TTL,
    )
    session.add(invite)
    await session.commit()
    await session.refresh(invite)
    log.info("groups.adult_invite.created", group_id=group.id, invite_id=invite.id)
    # The only response that contains the raw bearer-like invitation URL must
    # never be reused from a browser or intermediary cache. List responses
    # deliberately contain metadata only.
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Pragma"] = "no-cache"
    response.headers["Referrer-Policy"] = "no-referrer"
    return AdultInviteCreateResponse(
        **_invite_response(invite, now).model_dump(),
        invite_url=(f"{settings.parent_web_base_url.rstrip('/')}/group-invite#token={token}"),
    )


@router.get(
    "/groups/{group_id}/adult-invitations",
    response_model=AdultInviteListResponse,
)
async def list_adult_invitations(
    group_id: str,
    current_user: CurrentUserDep,
    session: DbSessionDep,
    settings: Annotated[Settings, Depends(get_request_settings)],
    response: Response,
) -> AdultInviteListResponse:
    owner = await resolve_current_user_row(
        session,
        current_user,
        allowed_roles=_ADULT_ROLES,
        missing_user_status=status.HTTP_404_NOT_FOUND,
    )
    group = await _load_owned_group(
        session,
        group_id=group_id,
        owner_user_id=owner.id,
    )
    _require_shared_group_safety_controls(settings, group)
    rows = (
        (
            await session.execute(
                select(models.GroupAdultInvite)
                .where(models.GroupAdultInvite.group_id == group.id)
                .order_by(models.GroupAdultInvite.created_at.desc())
                .limit(100)
            )
        )
        .scalars()
        .all()
    )
    now = datetime.now(UTC)
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Pragma"] = "no-cache"
    return AdultInviteListResponse(items=[_invite_response(row, now) for row in rows])


@router.delete(
    "/groups/{group_id}/adult-invitations/{invite_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def revoke_adult_invitation(
    group_id: str,
    invite_id: str,
    current_user: CurrentUserDep,
    session: DbSessionDep,
    settings: Annotated[Settings, Depends(get_request_settings)],
) -> Response:
    owner = await resolve_current_user_row(
        session,
        current_user,
        allowed_roles=_ADULT_ROLES,
        missing_user_status=status.HTTP_404_NOT_FOUND,
    )
    group = await _load_owned_group(
        session,
        group_id=group_id,
        owner_user_id=owner.id,
    )
    _require_shared_group_safety_controls(settings, group)
    invite = (
        await session.execute(
            select(models.GroupAdultInvite)
            .where(
                models.GroupAdultInvite.id == invite_id,
                models.GroupAdultInvite.group_id == group.id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if invite is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invite not found")
    if invite.redeemed_at is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A redeemed invitation cannot be revoked.",
        )
    if invite.revoked_at is None:
        invite.revoked_at = datetime.now(UTC)
        invite.revoked_by_user_id = owner.id
        await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/groups/invitations/redeem",
    response_model=AdultInviteRedeemResponse,
)
async def redeem_adult_invitation(
    request_body: AdultInviteRedeemRequest,
    current_user: CurrentUserDep,
    session: DbSessionDep,
    settings: Annotated[Settings, Depends(get_request_settings)],
) -> AdultInviteRedeemResponse:
    parent = await resolve_current_user_row(
        session,
        current_user,
        allowed_roles={"parent"},
        missing_user_status=status.HTTP_404_NOT_FOUND,
    )
    if not 40 <= len(request_body.token) <= 128:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invitation token is invalid.",
        )
    try:
        await require_linked_current_parent_consent(session, parent_user_id=parent.id)
    except CurrentParentConsentRequiredError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=CURRENT_PARENT_CONSENT_REQUIRED_MESSAGE,
        ) from exc

    digest = hashlib.sha256(request_body.token.encode("utf-8")).hexdigest()
    # The token digest is an untrusted selector. Resolve only its group id
    # without taking a row lock, then join the common lifecycle lock order:
    # Group -> Invite -> Membership. Archive/revoke/remove paths use the same
    # group-first order, so no membership can be created after archival and
    # no invite/member lock inversion can deadlock those operations.
    invite_group_id = (
        await session.execute(
            select(models.GroupAdultInvite.group_id).where(
                models.GroupAdultInvite.token_sha256 == digest
            )
        )
    ).scalar_one_or_none()
    if invite_group_id is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invite not found")
    group = (
        await session.execute(
            select(models.Group).where(models.Group.id == invite_group_id).with_for_update()
        )
    ).scalar_one_or_none()
    if group is None or group.archived_at is not None:
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="Invite is no longer active")
    _require_shared_groups(settings, group.id)
    invite = (
        await session.execute(
            select(models.GroupAdultInvite)
            .where(
                models.GroupAdultInvite.token_sha256 == digest,
                models.GroupAdultInvite.group_id == group.id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if invite is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invite not found")

    now = datetime.now(UTC)
    replayed = invite.redeemed_at is not None
    if replayed:
        if invite.redeemed_by_user_id != parent.id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Invite has already been used.",
            )
    elif invite.revoked_at is not None or invite.expires_at <= now:
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="Invite is no longer active")

    membership = (
        await session.execute(
            select(models.Membership)
            .where(
                models.Membership.group_id == group.id,
                models.Membership.user_id == parent.id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if replayed:
        if membership is None or membership.status != "active":
            raise HTTPException(
                status_code=status.HTTP_410_GONE,
                detail="A new invitation is required.",
            )
        return AdultInviteRedeemResponse(group_id=group.id, replayed=True)
    if membership is None:
        membership = models.Membership(
            id=str(ULID()),
            group_id=group.id,
            user_id=parent.id,
            role="parent",
            status="active",
        )
        session.add(membership)
        await session.flush()
    elif membership.status == "left":
        membership.status = "active"
        membership.left_at = None

    if invite.redeemed_at is None:
        invite.redeemed_at = now
        invite.redeemed_by_user_id = parent.id
    await session.commit()
    await session.refresh(membership)
    log.info(
        "groups.adult_invite.redeemed",
        group_id=group.id,
        invite_id=invite.id,
    )
    return AdultInviteRedeemResponse(group_id=group.id, replayed=False)


# ---------------------------------------------------------------------------
# POST /v1/groups/join -- legacy reusable join-code compatibility
# ---------------------------------------------------------------------------


class GroupJoinRequest(BaseModel):
    # Accept any 6-char string here; mismatches resolve to 404 at lookup time.
    # The Crockford alphabet check is intentionally not enforced in the schema
    # so a malformed code returns "code not found" rather than a 422 telling
    # an end user about our internal alphabet choice.
    join_code: str = Field(..., min_length=6, max_length=6)


@router.post(
    "/groups/join",
    response_model=MembershipResponse,
)
async def join_group(
    request_body: GroupJoinRequest,
    current_user: CurrentUserDep,
    session: DbSessionDep,
    settings: Annotated[Settings, Depends(get_request_settings)],
) -> MembershipResponse:
    """Redeem a 6-char join code; create a membership for the calling user.

    Used by adults joining an existing group (e.g. a co-parent joining the
    family group, or a co-teacher joining a class). Kids never use the join
    code path -- they're admin-created via the kid-provisioning endpoint.

    Idempotent: if the calling user is already a member of the matched
    group, returns the existing membership row without inserting a duplicate.
    The join code does not expire and does not consume on redeem; the
    `uq_memberships_group_user` unique constraint is the durable backstop
    against duplicates.
    """
    user = await resolve_current_user_row(
        session,
        current_user,
        allowed_roles=_ADULT_ROLES,
        missing_user_status=status.HTTP_404_NOT_FOUND,
    )

    # Normalize to upper-case so a parent typing the code by hand isn't
    # tripped up by lowercase input.
    candidate_code = request_body.join_code.upper()
    group_result = await session.execute(
        select(models.Group)
        .where(
            models.Group.join_code == candidate_code,
            models.Group.archived_at.is_(None),
        )
        .with_for_update()
    )
    group = group_result.scalar_one_or_none()
    if group is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="That join code doesn't match any group.",
        )
    if settings.shared_groups_allowed_for(group.id) or group.shared_groups_enabled_at is not None:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="This group uses private parent invitations.",
        )

    if user.role == "parent":
        try:
            await require_linked_current_parent_consent(session, parent_user_id=user.id)
        except CurrentParentConsentRequiredError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=CURRENT_PARENT_CONSENT_REQUIRED_MESSAGE,
            ) from exc

    membership_result = await session.execute(
        select(models.Membership).where(
            models.Membership.group_id == group.id,
            models.Membership.user_id == user.id,
        )
    )
    existing_membership = membership_result.scalar_one_or_none()
    if existing_membership is not None:
        if existing_membership.status == "left":
            existing_membership.status = "active"
            existing_membership.left_at = None
            await session.commit()
            await session.refresh(existing_membership)
        log.info(
            "groups.join.idempotent",
            group_id=group.id,
            user_id=user.id,
            membership_id=existing_membership.id,
        )
        return MembershipResponse.from_model(existing_membership)

    membership = models.Membership(
        id=str(ULID()),
        group_id=group.id,
        user_id=user.id,
        role=user.role,
        status="active",
    )
    session.add(membership)
    await session.commit()
    await session.refresh(membership)

    log.info(
        "groups.join.created",
        group_id=group.id,
        user_id=user.id,
        membership_id=membership.id,
        role=user.role,
    )
    return MembershipResponse.from_model(membership)
