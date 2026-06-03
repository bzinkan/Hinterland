"""Unit tests for the moderation Moderator providers."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.moderation.provider import (
    AzureContentSafetyModerator,
    ModerationUnavailable,
    NoOpModerator,
)

# ---------------------------------------------------------------------------
# NoOpModerator
# ---------------------------------------------------------------------------


async def test_noop_always_clean() -> None:
    moderator = NoOpModerator()
    result = await moderator.moderate(b"any-bytes")
    assert result.decision == "clean"
    assert result.labels == {}


# ---------------------------------------------------------------------------
# AzureContentSafetyModerator
# ---------------------------------------------------------------------------


def _build_moderator(severity_threshold: int = 4) -> AzureContentSafetyModerator:
    return AzureContentSafetyModerator(
        endpoint="https://example-cs.cognitiveservices.azure.com",
        key="test-key",
        timeout=2.0,
        severity_threshold=severity_threshold,
    )


def _mock_transport(handler: Any) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


def _patch_async_client(monkeypatch: pytest.MonkeyPatch, transport: httpx.MockTransport) -> None:
    original = httpx.AsyncClient

    def factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = transport
        return original(*args, **kwargs)

    monkeypatch.setattr("app.moderation.provider.httpx.AsyncClient", factory)


async def test_acs_missing_credentials_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    moderator = AzureContentSafetyModerator(
        endpoint="",
        key="",
        timeout=2.0,
    )
    with pytest.raises(ModerationUnavailable):
        await moderator.moderate(b"...")


async def test_acs_all_low_severity_returns_clean(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "categoriesAnalysis": [
                    {"category": "Hate", "severity": 0},
                    {"category": "SelfHarm", "severity": 0},
                    {"category": "Sexual", "severity": 2},
                    {"category": "Violence", "severity": 2},
                ]
            },
        )

    _patch_async_client(monkeypatch, _mock_transport(handler))
    moderator = _build_moderator(severity_threshold=4)
    result = await moderator.moderate(b"fake-image")
    assert result.decision == "clean"
    assert result.labels["Sexual"] == "2"


async def test_acs_high_severity_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "categoriesAnalysis": [
                    {"category": "Hate", "severity": 0},
                    {"category": "SelfHarm", "severity": 0},
                    {"category": "Sexual", "severity": 6},
                    {"category": "Violence", "severity": 0},
                ]
            },
        )

    _patch_async_client(monkeypatch, _mock_transport(handler))
    moderator = _build_moderator(severity_threshold=4)
    result = await moderator.moderate(b"fake-image")
    assert result.decision == "flagged"
    assert result.labels["Sexual"] == "6"


async def test_acs_unauthorized_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "unauthorized"})

    _patch_async_client(monkeypatch, _mock_transport(handler))
    moderator = _build_moderator()
    with pytest.raises(ModerationUnavailable):
        await moderator.moderate(b"fake-image")


async def test_acs_5xx_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="upstream busy")

    _patch_async_client(monkeypatch, _mock_transport(handler))
    moderator = _build_moderator()
    with pytest.raises(ModerationUnavailable):
        await moderator.moderate(b"fake-image")


async def test_acs_threshold_boundary_is_inclusive(monkeypatch: pytest.MonkeyPatch) -> None:
    """severity == threshold should flag (>= not >)."""

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "categoriesAnalysis": [
                    {"category": "Hate", "severity": 4},
                ]
            },
        )

    _patch_async_client(monkeypatch, _mock_transport(handler))
    moderator = _build_moderator(severity_threshold=4)
    result = await moderator.moderate(b"fake-image")
    assert result.decision == "flagged"
