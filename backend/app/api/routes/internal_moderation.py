"""Internal moderation endpoint -- manual / admin retry path only.

The production path under ADR 0010 (Azure) is event-driven and does NOT
call this HTTP route:

    Blob `pending/<id>.jpg` finalize
        -> Event Grid system topic
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
    """Direct-trigger shape. Eventarc CloudEvent unpacking lives below."""

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
        )
    except PhotoNotFound as exc:
        # Race with presign commit. 404 lets Eventarc retry.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Photo row not found for {payload.object_name}",
        ) from exc
    except ModerationUnavailable as exc:
        # 503 lets Eventarc retry. We never default-allow on outage --
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

    return ProcessResponse(
        photo_id=result.photo_id,
        decision=result.decision,
        new_object_name=result.new_object_name,
        review_queue_id=result.review_queue_id,
    )
