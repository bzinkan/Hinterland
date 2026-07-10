"""Internal moderation endpoint -- manual / admin retry path only.

The production path under ADR 0010 (Azure) does NOT call this HTTP route:

    observation + photo + moderation-outbox transaction commits
        -> moderation outbox relay
        -> Service Bus queue `moderation-pending`
        -> `dragonfly-moderation-worker` Container App (KEDA-scaled)
        -> `app.moderation.processor.process_pending_photo(...)` direct
           service call under managed identity (no HTTP hop, no /internal
           round trip).

This endpoint is retained for **manual / admin retries and smoke
testing**. It is not on the production trust boundary.

Transitional auth: the router still carries the GCP-era
`require_internal_oidc` dependency. That seam is being moved to an
Azure HMAC signature (Key Vault secret) in a follow-up alongside the
Service Bus consumer; until then the dependency is a soft no-op on
local dev and the route's only callers are operator-driven.
"""

from __future__ import annotations

from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.core.config import Settings, get_request_settings
from app.core.internal_auth import require_internal_oidc
from app.core.storage import SignedUrlGeneratorDep
from app.db.session import DbSessionDep
from app.moderation.processor import (
    ModerationWorkInvalid,
    PhotoNotFound,
    process_pending_photo,
)
from app.moderation.provider import ModerationUnavailable, ModeratorDep

router = APIRouter(
    prefix="/internal/moderation",
    tags=["internal"],
    dependencies=[Depends(require_internal_oidc)],
)

log = structlog.get_logger()


class ProcessRequest(BaseModel):
    """Manual retry of an already-committed moderation outbox item."""

    observation_id: str = Field(..., min_length=26, max_length=26)
    photo_id: str = Field(..., min_length=26, max_length=26)
    bucket: str = Field(..., min_length=1)
    object_name: str = Field(..., min_length=1)


class ProcessResponse(BaseModel):
    photo_id: str
    decision: str
    new_object_name: str | None
    review_queue_id: str | None


@router.post("/process", response_model=ProcessResponse)
async def moderation_process(
    payload: ProcessRequest,
    session: DbSessionDep,
    storage: SignedUrlGeneratorDep,
    moderator: ModeratorDep,
    settings: Annotated[Settings, Depends(get_request_settings)],
) -> ProcessResponse:
    try:
        result = await process_pending_photo(
            session,
            storage,
            moderator,
            bucket=payload.bucket,
            object_name=payload.object_name,
            settings=settings,
            expected_photo_id=payload.photo_id,
            expected_observation_id=payload.observation_id,
        )
    except PhotoNotFound as exc:
        # Manual caller can retry after investigating the committed row.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Photo row not found for {payload.object_name}",
        ) from exc
    except ModerationUnavailable as exc:
        # A manual caller receives 503. We never default-allow on outage --
        # docs/moderation.md "failed moderation does not default-allow".
        log.warning(
            "moderation.process.unavailable",
            object_name=payload.object_name,
            reason=str(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Moderation provider unavailable",
        ) from exc
    except ModerationWorkInvalid as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Moderation work does not match a committed observation",
        ) from exc

    return ProcessResponse(
        photo_id=result.photo_id,
        decision=result.decision,
        new_object_name=result.new_object_name,
        review_queue_id=result.review_queue_id,
    )
