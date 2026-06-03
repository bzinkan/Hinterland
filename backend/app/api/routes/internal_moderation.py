"""Internal moderation endpoint, called by the Eventarc GCS trigger.

Production path (when wired):
    GCS object finalized in `pending/<id>.jpg`
        -> Eventarc CloudEvent
        -> POST /internal/moderation/process

Pre-Eventarc dev path: trigger manually via the same endpoint with a
JSON body, e.g. for the smoke test or admin recovery.

Auth: the router carries a `require_internal_oidc` dependency that
verifies a Google-signed OIDC ID token, pins the audience, and gates
by an allowlist of service-account emails. Local dev opts out via
`Settings.require_internal_oidc` returning False; deployed envs fail
closed by default. See `app/core/internal_auth.py`.
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
