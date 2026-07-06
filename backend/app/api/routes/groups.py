"""Group create + kid-provisioning + join routes."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from ulid import ULID

from app.core.auth import CurrentUserDep, resolve_current_user_row
from app.core.config import Settings, get_request_settings
from app.core.kid_jwt import mint_handoff_token
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

_GROUP_OWNER_ROLES = frozenset({"parent", "teacher"})


def generate_join_code() -> str:
    """Generate a 6-char Crockford-base32 join code using a CSPRNG."""
    return "".join(secrets.choice(_JOIN_CODE_ALPHABET) for _ in range(_JOIN_CODE_LENGTH))


class GroupCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)


class GroupResponse(BaseModel):
    """Public shape of a `groups` row over the API."""

    id: str
    name: str
    join_code: str
    owner_user_id: str

    @classmethod
    def from_model(cls, group: models.Group) -> GroupResponse:
        return cls(
            id=group.id,
            name=group.name,
            join_code=group.join_code,
            owner_user_id=group.owner_user_id,
        )


@router.post(
    "/groups",
    response_model=GroupResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_group(
    request_body: GroupCreateRequest,
    current_user: CurrentUserDep,
    session: DbSessionDep,
) -> GroupResponse:
    """Create a group owned by the calling parent or teacher.

    Authorization is gated on the canonical `users.role` from Postgres rather
    than a cached token claim, so a parent who just signed up can create a
    group without waiting for a token refresh.

    Returns the new group with its 6-char join code. The code uses Crockford
    base32 (no I/L/O/U) so it's unambiguous when read aloud or typed by hand.
    Collisions are detected before commit and retried up to 5 times; the
    32^6 ≈ 1B code space makes that effectively never trigger at our scale.

    Idempotency is **not** offered here -- a parent who calls `POST /v1/groups`
    twice creates two groups. Phase 1's family flow creates exactly one group
    per parent, so the client should gate the call.
    """
    user = await resolve_current_user_row(
        session,
        current_user,
        missing_user_status=status.HTTP_404_NOT_FOUND,
    )

    if user.role not in _GROUP_OWNER_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Role '{user.role}' cannot create groups.",
        )

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

    log.info(
        "groups.create",
        group_id=group.id,
        owner_user_id=user.id,
        owner_role=user.role,
    )
    return GroupResponse.from_model(group)


# ---------------------------------------------------------------------------
# GET /v1/groups -- list groups the caller belongs to
# ---------------------------------------------------------------------------


class GroupListResponse(BaseModel):
    items: list[GroupResponse]


@router.get("/groups", response_model=GroupListResponse)
async def list_groups(
    current_user: CurrentUserDep,
    session: DbSessionDep,
) -> GroupListResponse:
    """List every non-archived group the caller has a membership in.

    Used by the adult-console group picker. Both groups the caller owns
    and groups the caller joined as a co-parent / co-teacher show up --
    we don't distinguish here; the consumer can compare `owner_user_id`
    to the caller's `id` if it cares.

    Order is newest-group-first so a freshly-created group is at the top.
    """
    user = await resolve_current_user_row(
        session,
        current_user,
        missing_user_status=status.HTTP_404_NOT_FOUND,
    )

    result = await session.execute(
        select(models.Group)
        .join(models.Membership, models.Membership.group_id == models.Group.id)
        .where(
            models.Membership.user_id == user.id,
            models.Group.archived_at.is_(None),
        )
        .order_by(models.Group.created_at.desc())
    )
    groups = result.scalars().all()
    return GroupListResponse(items=[GroupResponse.from_model(g) for g in groups])


# ---------------------------------------------------------------------------
# GET /v1/groups/{group_id}/members -- class roster
# ---------------------------------------------------------------------------


class RosterMember(BaseModel):
    """One row of the class roster -- the user + their membership context."""

    user_id: str
    display_name: str
    role: str
    age_band: str | None
    membership_id: str
    observation_count: int
    dex_count: int
    rarest_tier: str | None
    last_observed_at: datetime | None


class RosterResponse(BaseModel):
    group: GroupResponse
    items: list[RosterMember]


@router.get(
    "/groups/{group_id}/members",
    response_model=RosterResponse,
)
async def list_group_members(
    group_id: str,
    current_user: CurrentUserDep,
    session: DbSessionDep,
) -> RosterResponse:
    """Return every member of `group_id` along with their progress counters.

    Authorization: caller must already be a member of the group. Kids in
    the group can read this too (so the kid app can show "you and your
    classmates"); the response carries no PII beyond display names and
    age bands, which the kid already sees in the join flow.

    Order: adults first (parents, teachers), then kids alphabetically by
    display name. Stable so the UI doesn't shuffle on refresh.
    """
    caller = await resolve_current_user_row(
        session,
        current_user,
        missing_user_status=status.HTTP_404_NOT_FOUND,
    )

    group_result = await session.execute(select(models.Group).where(models.Group.id == group_id))
    group = group_result.scalar_one_or_none()
    if group is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Group '{group_id}' not found.",
        )

    caller_membership_result = await session.execute(
        select(models.Membership.id).where(
            models.Membership.group_id == group.id,
            models.Membership.user_id == caller.id,
        )
    )
    if caller_membership_result.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not a member of this group.",
        )

    members_result = await session.execute(
        select(models.Membership, models.User)
        .join(models.User, models.User.id == models.Membership.user_id)
        .where(models.Membership.group_id == group.id)
    )
    # Materialize rows as plain tuples up front so the sort key has a
    # stable, mypy-friendly type (Result.all() returns Row[tuple[...]],
    # which sorted() doesn't accept directly).
    rows: list[tuple[models.Membership, models.User]] = [(m, u) for m, u in members_result.all()]

    def sort_key(row: tuple[models.Membership, models.User]) -> tuple[int, str]:
        # 0 -> adults first, 1 -> kids; then alpha by display name (case-insensitive).
        membership, user = row
        return (1 if membership.role == "kid" else 0, user.display_name.lower())

    items = [
        RosterMember(
            user_id=u.id,
            display_name=u.display_name,
            role=m.role,
            age_band=u.age_band,
            membership_id=m.id,
            observation_count=m.observation_count,
            dex_count=m.dex_count,
            rarest_tier=m.rarest_tier,
            last_observed_at=m.last_observed_at,
        )
        for m, u in sorted(rows, key=sort_key)
    ]
    return RosterResponse(group=GroupResponse.from_model(group), items=items)


# ---------------------------------------------------------------------------
# POST /v1/groups/{group_id}/kids -- admin-create a kid via Firebase Admin SDK
# ---------------------------------------------------------------------------


_KID_PROVISIONER_ROLES = frozenset({"parent", "teacher"})


class KidCreateRequest(BaseModel):
    display_name: str = Field(..., min_length=1, max_length=80)
    age_band: AgeBand


class KidCreateResponse(BaseModel):
    """Public shape of a freshly-provisioned kid + their handoff JWT.

    The `handoff_token` is a single-use Hinterland-signed RS256 JWT (typ
    `handoff`, 15-minute TTL). The parent hands it to the kid's device via
    QR code / NFC; the kid's app POSTs it to `/v1/auth/kid-exchange` to
    swap it for a long-lived session JWT. The handoff JWT's `jti` is
    consumed atomically on first exchange.
    """

    id: str
    firebase_uid: str | None = None
    display_name: str
    age_band: str
    handoff_token: str
    expires_at: datetime


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
) -> KidCreateResponse:
    """Admin-create a kid account inside a group and return a handoff JWT.

    Authorization: caller must have a `users` row with role parent or teacher
    AND must own the target group. Phase 1 keeps this strict (only the owner)
    -- co-parent / co-teacher flows can ride the join-code redemption path.

    Side effects (in order):
    1. Insert `users` row (firebase_uid=NULL, entra_oid=NULL; kids have no
       external IdP identity in the post-Firebase world).
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

    group_result = await session.execute(select(models.Group).where(models.Group.id == group_id))
    group = group_result.scalar_one_or_none()
    if group is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Group '{group_id}' not found.",
        )
    if group.owner_user_id != caller.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the group owner can provision kids in this group.",
        )

    # Kids have no external IdP identity: no Firebase uid, no Entra OID.
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
        settings=settings,
    )
    handoff_expires_at = datetime.now(UTC) + timedelta(
        seconds=settings.dragonfly_handoff_ttl_seconds
    )

    log.info(
        "groups.create_kid",
        group_id=group.id,
        kid_id=kid.id,
        parent_id=caller.id,
        jti=_jti,
    )

    return KidCreateResponse(
        id=kid.id,
        firebase_uid=None,
        display_name=kid.display_name,
        age_band=str(kid.age_band),
        handoff_token=handoff_token,
        expires_at=handoff_expires_at,
    )


# ---------------------------------------------------------------------------
# POST /v1/groups/join -- redeem a 6-char join code
# ---------------------------------------------------------------------------


class GroupJoinRequest(BaseModel):
    # Accept any 6-char string here; mismatches resolve to 404 at lookup time.
    # The Crockford alphabet check is intentionally not enforced in the schema
    # so a malformed code returns "code not found" rather than a 422 telling
    # an end user about our internal alphabet choice.
    join_code: str = Field(..., min_length=6, max_length=6)


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


@router.post(
    "/groups/join",
    response_model=MembershipResponse,
)
async def join_group(
    request_body: GroupJoinRequest,
    current_user: CurrentUserDep,
    session: DbSessionDep,
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
        missing_user_status=status.HTTP_404_NOT_FOUND,
    )

    # Normalize to upper-case so a parent typing the code by hand isn't
    # tripped up by lowercase input.
    candidate_code = request_body.join_code.upper()
    group_result = await session.execute(
        select(models.Group).where(models.Group.join_code == candidate_code)
    )
    group = group_result.scalar_one_or_none()
    if group is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="That join code doesn't match any group.",
        )

    membership_result = await session.execute(
        select(models.Membership).where(
            models.Membership.group_id == group.id,
            models.Membership.user_id == user.id,
        )
    )
    existing_membership = membership_result.scalar_one_or_none()
    if existing_membership is not None:
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
