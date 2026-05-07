"""Authenticated user routes."""

from __future__ import annotations

from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from ulid import ULID

from app.core.auth import (
    CurrentUser,
    CurrentUserDep,
    set_firebase_custom_claims,
)
from app.core.config import Settings, get_request_settings
from app.db import models
from app.db.session import DbSessionDep

router = APIRouter(prefix="/v1", tags=["auth"])

log = structlog.get_logger()


class ParentSignupRequest(BaseModel):
    display_name: str = Field(..., min_length=1, max_length=80)


class UserResponse(BaseModel):
    """Public shape of a `users` row over the API."""

    id: str
    firebase_uid: str
    role: str
    display_name: str

    @classmethod
    def from_model(cls, user: models.User) -> "UserResponse":
        return cls(
            id=user.id,
            firebase_uid=user.firebase_uid,
            role=user.role,
            display_name=user.display_name,
        )


@router.get("/me", response_model=CurrentUser)
def me(current_user: CurrentUserDep) -> CurrentUser:
    return current_user


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
    """Create or return the parent `users` row for the authenticated Firebase ID.

    The client is expected to have already created the Firebase user via
    email/password (Firebase Web SDK). This endpoint:

    1. Reads the verified Firebase ID token from the `Authorization` header.
    2. Upserts a `users` row with `role='parent'` keyed by the Firebase uid.
    3. Sets the Firebase custom claim `role=parent` so subsequent ID tokens
       carry the role without a server lookup.

    Idempotent: if a `users` row already exists for this firebase_uid, the
    existing row is returned and no new `users` row is created. The custom
    claim is re-set on every call to recover from drift (e.g. claims wiped
    by a manual Console action).
    """
    result = await session.execute(
        select(models.User).where(models.User.firebase_uid == current_user.uid)
    )
    existing = result.scalar_one_or_none()

    if existing is not None:
        set_firebase_custom_claims(current_user.uid, {"role": "parent"}, settings)
        log.info(
            "auth.parent_signup.idempotent",
            user_id=existing.id,
            firebase_uid=current_user.uid,
        )
        return UserResponse.from_model(existing)

    new_user = models.User(
        id=str(ULID()),
        firebase_uid=current_user.uid,
        role="parent",
        display_name=request_body.display_name,
    )
    session.add(new_user)
    await session.commit()
    await session.refresh(new_user)

    set_firebase_custom_claims(current_user.uid, {"role": "parent"}, settings)
    log.info(
        "auth.parent_signup.created",
        user_id=new_user.id,
        firebase_uid=current_user.uid,
    )
    return UserResponse.from_model(new_user)
