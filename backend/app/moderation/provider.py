"""Pluggable photo-moderation providers.

`NoOpModerator`            -- treats every photo as clean. Default in dev / CI.
`AzureContentSafetyModerator` -- production. Calls Azure AI Content Safety
   `image:analyze`. Per ADR 0010 the quarantine threshold is
   `severity >= settings.content_safety_severity_threshold` (default 4 /
   Medium). The four standard categories (Hate, SelfHarm, Sexual,
   Violence) cover what SafeSearch's adult / racy / violence / medical
   used to before the Phase 6c rewrite.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from typing import Annotated, Literal, Protocol, cast

import httpx
import structlog
from fastapi import Depends, Request

from app.core.config import Settings

log = structlog.get_logger()


class ModerationUnavailable(Exception):
    """Raised when the moderation provider is unreachable.

    Per docs/moderation.md the worker MUST raise (not default-allow)
    so the moderation queue retries.
    """


Decision = Literal["clean", "flagged"]


@dataclass(frozen=True)
class ModerationResult:
    decision: Decision
    labels: dict[str, str] = field(default_factory=dict)


class Moderator(Protocol):
    async def moderate(self, image_bytes: bytes) -> ModerationResult:
        """Classify the image. Raises ModerationUnavailable on outage."""
        ...


class NoOpModerator:
    async def moderate(self, image_bytes: bytes) -> ModerationResult:
        return ModerationResult(decision="clean", labels={})


class AzureContentSafetyModerator:
    """Azure AI Content Safety implementation of the Moderator protocol.

    Uses the image:analyze REST endpoint with a subscription key (the
    Container App receives the key as an env var sourced from Key Vault
    via the UAMI). Maps the four Content Safety categories (Hate,
    SelfHarm, Sexual, Violence) onto a single quarantine decision at
    `severity >= settings.content_safety_severity_threshold` (default
    4 / Medium per ADR 0010).
    """

    API_VERSION = "2023-10-01"
    CATEGORIES = ("Hate", "SelfHarm", "Sexual", "Violence")

    def __init__(
        self,
        *,
        endpoint: str,
        key: str,
        timeout: float,
        severity_threshold: int = 4,
    ) -> None:
        self._endpoint = endpoint.rstrip("/")
        self._key = key
        self._timeout = timeout
        self._severity_threshold = severity_threshold

    async def moderate(self, image_bytes: bytes) -> ModerationResult:
        if not self._endpoint or not self._key:
            raise ModerationUnavailable("Content Safety endpoint/key missing -- check KV secrets.")
        encoded = base64.b64encode(image_bytes).decode("ascii")
        body = {
            "image": {"content": encoded},
            "categories": list(self.CATEGORIES),
            "outputType": "FourSeverityLevels",
        }
        url = f"{self._endpoint}/contentsafety/image:analyze?api-version={self.API_VERSION}"

        try:
            async with httpx.AsyncClient(
                timeout=self._timeout,
                headers={
                    "Ocp-Apim-Subscription-Key": self._key,
                    "Content-Type": "application/json",
                },
            ) as client:
                res = await client.post(url, json=body)
        except (httpx.TransportError, httpx.TimeoutException) as exc:
            log.warning("moderation.acs.transport_error", error=str(exc))
            raise ModerationUnavailable("Content Safety transport error") from exc

        if res.status_code in (401, 403):
            log.warning("moderation.acs.unauthorized", status=res.status_code)
            raise ModerationUnavailable(f"Content Safety unauthorized: {res.status_code}")
        if res.status_code >= 500:
            log.warning("moderation.acs.server_error", status=res.status_code)
            raise ModerationUnavailable(f"Content Safety server error: {res.status_code}")
        if res.status_code != 200:
            log.warning(
                "moderation.acs.client_error",
                status=res.status_code,
                body=res.text[:200],
            )
            raise ModerationUnavailable(f"Content Safety client error: {res.status_code}")

        payload = cast(dict[str, object], res.json())
        analyses = payload.get("categoriesAnalysis")
        if not isinstance(analyses, list):
            return ModerationResult(decision="clean", labels={})

        labels: dict[str, str] = {}
        flagged: dict[str, str] = {}
        for entry in analyses:
            if not isinstance(entry, dict):
                continue
            category = entry.get("category")
            severity = entry.get("severity", 0)
            if not isinstance(category, str) or not isinstance(severity, int):
                continue
            labels[category] = str(severity)
            if severity >= self._severity_threshold:
                flagged[category] = str(severity)

        decision: Decision = "flagged" if flagged else "clean"
        log.info(
            "moderation.acs.classified",
            decision=decision,
            labels=labels,
            flagged=flagged,
            threshold=self._severity_threshold,
        )
        return ModerationResult(decision=decision, labels=labels)


def build_moderator(settings: Settings) -> Moderator:
    if settings.moderation_provider == "azure_content_safety":
        return AzureContentSafetyModerator(
            endpoint=settings.content_safety_endpoint,
            key=settings.content_safety_key,
            timeout=settings.content_safety_request_timeout_seconds,
            severity_threshold=settings.content_safety_severity_threshold,
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
