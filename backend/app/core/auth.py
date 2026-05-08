"""Firebase Auth verification and API identity dependencies."""

from __future__ import annotations

from typing import Annotated, Literal, cast, get_args

import firebase_admin
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from firebase_admin import auth as firebase_auth
from pydantic import BaseModel

from app.core.config import Settings, get_request_settings

UserRole = Literal["kid", "parent", "teacher", "admin"]
_USER_ROLES = set(get_args(UserRole))

bearer_scheme = HTTPBearer(auto_error=False)


class CurrentUser(BaseModel):
    uid: str
    email: str | None = None
    role: UserRole | None = None
    group_id: str | None = None
    kid_id: str | None = None
    parent_id: str | None = None
    teacher_id: str | None = None


class InvalidAuthToken(Exception):
    """Raised when a Firebase token cannot be trusted."""


def _firebase_project_id(settings: Settings) -> str:
    return settings.firebase_project_id or settings.gcp_project_id


def _firebase_app(settings: Settings) -> firebase_admin.App:
    project_id = _firebase_project_id(settings)
    app_name = f"dragonfly-{project_id}"
    try:
        return firebase_admin.get_app(app_name)
    except ValueError:
        return firebase_admin.initialize_app(
            options={"projectId": project_id},
            name=app_name,
        )


def verify_firebase_id_token(token: str, settings: Settings) -> dict[str, object]:
    """Verify a Firebase ID token and return decoded claims."""
    try:
        claims = firebase_auth.verify_id_token(
            token,
            app=_firebase_app(settings),
            check_revoked=settings.firebase_check_revoked,
        )
    except Exception as exc:  # Firebase Admin raises several auth-specific subclasses.
        raise InvalidAuthToken("Invalid bearer token") from exc

    if not isinstance(claims, dict):
        raise InvalidAuthToken("Invalid bearer token")
    return cast(dict[str, object], claims)


def set_firebase_custom_claims(
    uid: str,
    claims: dict[str, object],
    settings: Settings,
) -> None:
    """Set custom claims on a Firebase user via the Admin SDK.

    Custom claims propagate into the user's next ID token (after the client
    refreshes its token). The full claim set is replaced on each call --
    callers should pass the complete intended state, not deltas.
    """
    firebase_auth.set_custom_user_claims(uid, claims, app=_firebase_app(settings))


def create_firebase_user(
    *,
    display_name: str,
    settings: Settings,
) -> str:
    """Create a Firebase user with no email (for kid accounts) and return its UID.

    Kids never have email addresses. The returned UID is the canonical
    identity; the caller is responsible for storing it on the corresponding
    `users` row and setting custom claims.
    """
    record = firebase_auth.create_user(
        display_name=display_name,
        app=_firebase_app(settings),
    )
    return cast(str, record.uid)


def delete_firebase_user(uid: str, settings: Settings) -> None:
    """Delete a Firebase user by UID. Used for cleanup on partial-create failure.

    Swallows `UserNotFoundError` so it's safe to call defensively even if the
    user was never actually created.
    """
    try:
        firebase_auth.delete_user(uid, app=_firebase_app(settings))
    except firebase_auth.UserNotFoundError:
        return


def create_firebase_custom_token(uid: str, settings: Settings) -> str:
    """Mint a Firebase custom token the client can exchange for an ID token.

    Used to hand a freshly-provisioned kid account off to the kid's device:
    the parent gives the kid the custom token (or scans a QR), the kid's
    Firebase Web SDK calls `signInWithCustomToken`, and from then on the
    kid's app uses normal ID tokens.
    """
    token = firebase_auth.create_custom_token(uid, app=_firebase_app(settings))
    if isinstance(token, bytes):
        return token.decode("utf-8")
    return cast(str, token)


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
    uid = _claim_str(claims, "uid") or _claim_str(claims, "user_id") or _claim_str(claims, "sub")
    if uid is None:
        raise InvalidAuthToken("Firebase token is missing uid")

    return CurrentUser(
        uid=uid,
        email=_claim_str(claims, "email"),
        role=_claim_role(claims),
        group_id=_claim_str(claims, "group_id"),
        kid_id=_claim_str(claims, "kid_id"),
        parent_id=_claim_str(claims, "parent_id"),
        teacher_id=_claim_str(claims, "teacher_id"),
    )


def get_current_user(
    request: Request,
    settings: Annotated[Settings, Depends(get_request_settings)],
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> CurrentUser:
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        claims = verify_firebase_id_token(credentials.credentials, settings)
        return current_user_from_claims(claims)
    except InvalidAuthToken as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


CurrentUserDep = Annotated[CurrentUser, Depends(get_current_user)]
