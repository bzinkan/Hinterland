"""iNaturalist Computer Vision species suggestions."""

from __future__ import annotations

from dataclasses import dataclass

import httpx
import structlog

from app.inat.client import InatUnavailable

log = structlog.get_logger()


@dataclass(frozen=True)
class CvSuggestion:
    taxon_id: int
    common_name: str | None
    scientific_name: str | None
    score: float


async def score_image(
    client: httpx.AsyncClient,
    *,
    image_bytes: bytes,
    image_filename: str = "observation.jpg",
    top_k: int = 3,
    egress_enabled: bool = False,
) -> list[CvSuggestion]:
    """Call iNat /v1/computervision/score_image with raw image bytes.

    Returns up to `top_k` suggestions, highest-scoring first. Empty list is
    a legitimate response (iNat couldn't classify) -- callers should treat
    that as "no suggestions" rather than an error.

    Raises `InatUnavailable` on transport faults, 5xx, or unauthorized
    (401/403) so the route can degrade gracefully without leaking the
    underlying httpx exception type.
    """
    if not egress_enabled:
        log.warning("inat.cv.blocked_by_kill_switch")
        raise InatUnavailable("iNat CV photo egress is disabled")

    files = {"image": (image_filename, image_bytes, "image/jpeg")}
    try:
        res = await client.post("/computervision/score_image", files=files)
    except (httpx.TransportError, httpx.TimeoutException) as exc:
        log.warning("inat.cv.transport_error", error=str(exc))
        raise InatUnavailable("iNat CV transport error") from exc

    if res.status_code in (401, 403):
        log.warning("inat.cv.unauthorized", status=res.status_code)
        raise InatUnavailable(f"iNat CV unauthorized: {res.status_code}")
    if res.status_code == 429:
        log.warning("inat.cv.rate_limited")
        raise InatUnavailable("iNat CV rate limited")
    if res.status_code >= 500:
        log.warning("inat.cv.server_error", status=res.status_code)
        raise InatUnavailable(f"iNat CV server error: {res.status_code}")
    if res.status_code >= 400:
        # 4xx other than auth: log and treat as no-suggestions. Kid still
        # sees their observation; manual species selection remains.
        log.warning("inat.cv.client_error", status=res.status_code, body=res.text[:200])
        return []

    try:
        payload = res.json()
    except ValueError as exc:
        log.warning("inat.cv.malformed_success")
        raise InatUnavailable("iNat CV returned malformed JSON") from exc
    if not isinstance(payload, dict):
        log.warning("inat.cv.malformed_success")
        raise InatUnavailable("iNat CV returned a non-object response")

    raw_results = payload.get("results")
    if not isinstance(raw_results, list):
        log.warning("inat.cv.malformed_success")
        raise InatUnavailable("iNat CV returned malformed results")

    suggestions: list[CvSuggestion] = []
    for raw in raw_results[:top_k]:
        if not isinstance(raw, dict):
            raise InatUnavailable("iNat CV returned a malformed result")
        taxon = raw.get("taxon")
        if not isinstance(taxon, dict):
            raise InatUnavailable("iNat CV returned a malformed taxon")
        taxon_id = taxon.get("id")
        if isinstance(taxon_id, bool) or not isinstance(taxon_id, int):
            raise InatUnavailable("iNat CV returned a malformed taxon id")
        score_raw = raw.get("combined_score", raw.get("score"))
        if isinstance(score_raw, bool) or not isinstance(score_raw, int | float):
            raise InatUnavailable("iNat CV returned a malformed score")
        common = taxon.get("preferred_common_name") or taxon.get("name")
        scientific = taxon.get("name")
        suggestions.append(
            CvSuggestion(
                taxon_id=taxon_id,
                common_name=common if isinstance(common, str) else None,
                scientific_name=scientific if isinstance(scientific, str) else None,
                score=float(score_raw),
            )
        )
    return suggestions
