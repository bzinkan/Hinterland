"""Group create + join routes."""

from __future__ import annotations

import secrets

import structlog
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from ulid import ULID

from app.core.auth import CurrentUserDep
from app.db import models
from app.db.session import DbSessionDep

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
