"""Group create + kid-provisioning + join routes."""

from __future__ import annotations

import secrets
from typing import Annotated, Literal

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from ulid import ULID

from app.core.auth import (
    CurrentUserDep,
    create_firebase_custom_token,
    create_firebase_user,
    delete_firebase_user,
    set_firebase_custom_claims,
)
from app.core.config import Settings, get_request_settings
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
    than the Firebase ID-token claim, so a parent who just signed up doesn't
    have to refresh their token before creating a group. The custom claim is
    a convenience cache of the same fact.

    Returns the new group with its 6-char join code. The code uses Crockford
    base32 (no I/L/O/U) so it's unambiguous when read aloud or typed by hand.
    Collisions are detected before commit and retried up to 5 times; the
    32^6 ≈ 1B code space makes that effectively never trigger at our scale.

    Idempotency is **not** offered here -- a parent who calls `POST /v1/groups`
    twice creates two groups. Phase 1's family flow creates exactly one group
    per parent, so the client should gate the call.
    """
    user_result = await session.execute(
        select(models.User).where(models.User.firebase_uid == current_user.uid)
    )
    user = user_result.scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                "Calling Firebase user has no `users` row. "
                "Sign up via /v1/auth/parent-signup or the teacher equivalent first."
            ),
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
# POST /v1/groups/{group_id}/kids -- admin-create a kid via Firebase Admin SDK
# ---------------------------------------------------------------------------


_KID_PROVISIONER_ROLES = frozenset({"parent", "teacher"})


class KidCreateRequest(BaseModel):
    display_name: str = Field(..., min_length=1, max_length=80)
    age_band: AgeBand


class KidCreateResponse(BaseModel):
    """Public shape of a freshly-provisioned kid + the Firebase custom token.

    The custom token is one-time-use; the parent hands it to the kid's
    device, the kid's Firebase Web SDK calls `signInWithCustomToken`, and
    from then on the kid's app uses normal ID tokens.
    """

    id: str
    firebase_uid: str
    display_name: str
    age_band: str
    custom_token: str


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
    """Admin-create a kid account inside a group.

    Authorization: caller must have a `users` row with role parent or teacher
    AND must own the target group. Phase 1 keeps this strict (only the owner)
    -- co-parent / co-teacher flows can ride the join-code redemption path.

    Side effects (in order):
    1. Create a Firebase user with no email via Admin SDK.
    2. Set custom claims `{role: 'kid', group_id, parent_user_id}` so the
       kid's ID tokens carry their identity context once they sign in.
    3. Insert `users` and `memberships` rows in one transaction.
    4. Mint a Firebase custom token for the kid's first sign-in.

    On any failure after step 1, the Firebase user is best-effort deleted to
    avoid orphan auth records.
    """
    user_result = await session.execute(
        select(models.User).where(models.User.firebase_uid == current_user.uid)
    )
    caller = user_result.scalar_one_or_none()
    if caller is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                "Calling Firebase user has no `users` row. "
                "Sign up via /v1/auth/parent-signup or the teacher equivalent first."
            ),
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

    kid_firebase_uid = create_firebase_user(
        display_name=request_body.display_name,
        settings=settings,
    )

    try:
        set_firebase_custom_claims(
            kid_firebase_uid,
            {"role": "kid", "group_id": group.id, "parent_user_id": caller.id},
            settings,
        )

        kid = models.User(
            id=str(ULID()),
            firebase_uid=kid_firebase_uid,
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
        session.add(kid)
        session.add(membership)
        await session.commit()
        await session.refresh(kid)
    except Exception:
        # Best-effort cleanup so we don't leak a Firebase user that has no
        # corresponding `users` row. Swallow cleanup errors -- raising here
        # would mask the original cause.
        delete_firebase_user(kid_firebase_uid, settings)
        raise

    custom_token = create_firebase_custom_token(kid_firebase_uid, settings)

    log.info(
        "groups.create_kid",
        group_id=group.id,
        kid_id=kid.id,
        parent_id=caller.id,
    )

    return KidCreateResponse(
        id=kid.id,
        firebase_uid=kid.firebase_uid,
        display_name=kid.display_name,
        age_band=str(kid.age_band),
        custom_token=custom_token,
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
    user_result = await session.execute(
        select(models.User).where(models.User.firebase_uid == current_user.uid)
    )
    user = user_result.scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                "Calling Firebase user has no `users` row. "
                "Sign up via /v1/auth/parent-signup or the teacher equivalent first."
            ),
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
