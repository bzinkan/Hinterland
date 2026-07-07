"""Shared async httpx client for the iNaturalist API.

One client per FastAPI app instance, lazily constructed and cached on
`app.state.inat_client`. Tests inject a stub by setting that attribute
before the first request handler runs.
"""

from __future__ import annotations

from typing import Annotated

import httpx
from fastapi import Depends, Request

from app.core.config import Settings


class InatUnavailable(Exception):
    """Raised when iNat is unreachable or unauthorized.

    Routes catch this and fall back to graceful-degradation behavior
    (e.g. `cv_unavailable=True` in the response). Per ADR 0005 +
    `docs/architecture.md`, the kid experience cannot depend on iNat
    being reachable at the moment of submission.
    """


def build_inat_client(settings: Settings) -> httpx.AsyncClient:
    headers = {"User-Agent": "Hinterland/0.1 (+https://thehinterlandguide.app)"}
    if settings.inat_oauth_token:
        headers["Authorization"] = f"Bearer {settings.inat_oauth_token}"
    return httpx.AsyncClient(
        base_url=settings.inat_base_url,
        timeout=settings.inat_request_timeout_seconds,
        headers=headers,
    )


def get_inat_client(request: Request) -> httpx.AsyncClient:
    """Pull the cached client off `app.state`. Tests preset a stub there."""
    client = getattr(request.app.state, "inat_client", None)
    if client is None:
        settings: Settings = request.app.state.settings
        client = build_inat_client(settings)
        request.app.state.inat_client = client
    return client


InatClientDep = Annotated[httpx.AsyncClient, Depends(get_inat_client)]
