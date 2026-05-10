"""Pluggable moderation providers.

`NoOpModerator`   -- treats every photo as clean. Default in dev / CI.
`CloudVisionSafeSearchModerator` -- ADR 0009 production gate. Calls
   the Vision API REST endpoint via httpx with an ADC-derived bearer
   token. Per-label thresholds match the table in ADR 0009:

       adult     -> flag at LIKELY+
       violence  -> flag at LIKELY+
       racy      -> flag at LIKELY+
       medical   -> flag at VERY_LIKELY only
       spoof     -> ignored

`MODERATION_LIKELIHOODS` is the SafeSearch enum, ordered.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from typing import Annotated, Any, Literal, Protocol, cast

import google.auth
import google.auth.transport.requests
import httpx
import structlog
from fastapi import Depends, Request

from app.core.config import Settings

log = structlog.get_logger()

# Order matters -- index in this list IS the comparison rank.
MODERATION_LIKELIHOODS: tuple[str, ...] = (
    "UNKNOWN",
    "VERY_UNLIKELY",
    "UNLIKELY",
    "POSSIBLE",
    "LIKELY",
    "VERY_LIKELY",
)

# label -> minimum likelihood that triggers a flag, per ADR 0009.
DEFAULT_FLAG_THRESHOLDS: dict[str, str] = {
    "adult": "LIKELY",
    "violence": "LIKELY",
    "racy": "LIKELY",
    "medical": "VERY_LIKELY",
}


class ModerationUnavailable(Exception):
    """Raised when the moderation provider is unreachable.

    Per docs/moderation.md the worker MUST raise (not default-allow)
    so Eventarc retries; if retries are exhausted the GCS lifecycle
    rule on `pending/` cleans up after 24h.
    """


Decision = Literal["clean", "flagged"]


@dataclass(frozen=True)
class ModerationResult:
    decision: Decision
    labels: dict[str, str] = field(default_factory=dict)


def likelihood_at_or_above(value: str, threshold: str) -> bool:
    """SafeSearch likelihood comparison via the canonical enum order."""
    try:
        return MODERATION_LIKELIHOODS.index(value) >= MODERATION_LIKELIHOODS.index(threshold)
    except ValueError:
        return False


class Moderator(Protocol):
    async def moderate(self, image_bytes: bytes) -> ModerationResult:
        """Classify the image. Raises ModerationUnavailable on outage."""
        ...


class NoOpModerator:
    async def moderate(self, image_bytes: bytes) -> ModerationResult:
        return ModerationResult(decision="clean", labels={})


class CloudVisionSafeSearchModerator:
    def __init__(
        self,
        *,
        endpoint: str,
        timeout: float,
        thresholds: dict[str, str] | None = None,
    ) -> None:
        self._endpoint = endpoint.rstrip("/")
        self._timeout = timeout
        self._thresholds = thresholds or DEFAULT_FLAG_THRESHOLDS
        creds, _project = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"],
        )
        self._credentials: Any = creds

    async def moderate(self, image_bytes: bytes) -> ModerationResult:
        # Refresh checks expiry; cheap on the metadata server in Cloud Run.
        try:
            self._credentials.refresh(google.auth.transport.requests.Request())
            token: str = self._credentials.token
        except Exception as exc:  # google-auth raises various subclasses
            log.warning("moderation.cv.token_refresh_failed", error=str(exc))
            raise ModerationUnavailable("Could not refresh Google ADC token") from exc

        encoded = base64.b64encode(image_bytes).decode("ascii")
        body = {
            "requests": [
                {
                    "image": {"content": encoded},
                    "features": [{"type": "SAFE_SEARCH_DETECTION"}],
                }
            ]
        }

        try:
            async with httpx.AsyncClient(
                timeout=self._timeout,
                headers={"Authorization": f"Bearer {token}"},
            ) as client:
                res = await client.post(f"{self._endpoint}/images:annotate", json=body)
        except (httpx.TransportError, httpx.TimeoutException) as exc:
            log.warning("moderation.cv.transport_error", error=str(exc))
            raise ModerationUnavailable("Vision API transport error") from exc

        if res.status_code in (401, 403):
            log.warning("moderation.cv.unauthorized", status=res.status_code)
            raise ModerationUnavailable(f"Vision API unauthorized: {res.status_code}")
        if res.status_code >= 500:
            log.warning("moderation.cv.server_error", status=res.status_code)
            raise ModerationUnavailable(f"Vision API server error: {res.status_code}")
        if res.status_code != 200:
            # 4xx other -- bad image, malformed request. Treat as
            # unavailable so it retries; if it's truly malformed, it
            # will exhaust retries and the lifecycle rule cleans up.
            log.warning("moderation.cv.client_error", status=res.status_code, body=res.text[:200])
            raise ModerationUnavailable(f"Vision API client error: {res.status_code}")

        payload = cast(dict[str, object], res.json())
        responses = payload.get("responses")
        if not isinstance(responses, list) or not responses:
            return ModerationResult(decision="clean", labels={})
        first = responses[0]
        if not isinstance(first, dict):
            return ModerationResult(decision="clean", labels={})
        annotation = first.get("safeSearchAnnotation")
        if not isinstance(annotation, dict):
            return ModerationResult(decision="clean", labels={})

        labels: dict[str, str] = {}
        for key, value in annotation.items():
            if isinstance(value, str):
                labels[key] = value

        flagged_labels = {
            label: labels[label]
            for label, threshold in self._thresholds.items()
            if label in labels and likelihood_at_or_above(labels[label], threshold)
        }
        decision: Decision = "flagged" if flagged_labels else "clean"
        log.info(
            "moderation.cv.classified",
            decision=decision,
            labels=labels,
            flagged=flagged_labels,
        )
        return ModerationResult(decision=decision, labels=labels)


def build_moderator(settings: Settings) -> Moderator:
    if settings.moderation_provider == "cloud_vision_safesearch":
        return CloudVisionSafeSearchModerator(
            endpoint=settings.vision_api_endpoint,
            timeout=settings.vision_request_timeout_seconds,
        )
    return NoOpModerator()


def get_moderator(request: Request) -> Moderator:
    moderator = getattr(request.app.state, "moderator", None)
    if moderator is None:
        settings: Settings = request.app.state.settings
        moderator = build_moderator(settings)
        request.app.state.moderator = moderator
    return cast(Moderator, moderator)


ModeratorDep = Annotated[Moderator, Depends(get_moderator)]
