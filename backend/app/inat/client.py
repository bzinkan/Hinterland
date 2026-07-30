"""Purpose-scoped async httpx clients for the iNaturalist API.

Public taxonomy reads are unauthenticated. CV and public submission each use a
separate bearer credential and client so configuring one capability cannot
authorize the other.
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


def _build_inat_client(settings: Settings, *, token: str = "") -> httpx.AsyncClient:
    headers = {"User-Agent": "Hinterland/0.1 (+https://thehinterlandguide.app)"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return httpx.AsyncClient(
        base_url=settings.inat_base_url,
        timeout=settings.inat_request_timeout_seconds,
        headers=headers,
    )


def build_inat_client(settings: Settings) -> httpx.AsyncClient:
    """Build the backwards-compatible unauthenticated taxonomy client."""

    return _build_inat_client(settings)


def build_inat_cv_client(settings: Settings) -> httpx.AsyncClient:
    """Build a client carrying only the dedicated CV credential."""

    return _build_inat_client(settings, token=settings.inat_cv_token.strip())


def build_inat_submit_client(settings: Settings) -> httpx.AsyncClient:
    """Build a client carrying only the dedicated public-submit credential."""

    return _build_inat_client(settings, token=settings.inat_submit_token.strip())


def get_inat_cv_client(request: Request) -> httpx.AsyncClient:
    """Pull the cached CV-only client off ``app.state``."""

    client = getattr(request.app.state, "inat_cv_client", None)
    if client is None:
        settings: Settings = request.app.state.settings
        client = build_inat_cv_client(settings)
        request.app.state.inat_cv_client = client
    return client


InatCvClientDep = Annotated[httpx.AsyncClient, Depends(get_inat_cv_client)]
