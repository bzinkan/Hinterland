"""Unit tests for the moderation Moderator providers."""

from __future__ import annotations

from app.moderation.provider import (
    DEFAULT_FLAG_THRESHOLDS,
    NoOpModerator,
    likelihood_at_or_above,
)

# ---------------------------------------------------------------------------
# likelihood_at_or_above
# ---------------------------------------------------------------------------


def test_likelihood_comparison() -> None:
    assert likelihood_at_or_above("LIKELY", "LIKELY") is True
    assert likelihood_at_or_above("VERY_LIKELY", "LIKELY") is True
    assert likelihood_at_or_above("POSSIBLE", "LIKELY") is False
    assert likelihood_at_or_above("UNLIKELY", "LIKELY") is False
    # ADR 0009 medical-only-at-VERY_LIKELY semantics
    assert likelihood_at_or_above("LIKELY", "VERY_LIKELY") is False
    assert likelihood_at_or_above("VERY_LIKELY", "VERY_LIKELY") is True


def test_likelihood_unknown_value_safe() -> None:
    assert likelihood_at_or_above("BOGUS", "LIKELY") is False


def test_default_thresholds_match_adr_0009() -> None:
    assert DEFAULT_FLAG_THRESHOLDS == {
        "adult": "LIKELY",
        "violence": "LIKELY",
        "racy": "LIKELY",
        "medical": "VERY_LIKELY",
    }


# ---------------------------------------------------------------------------
# NoOpModerator
# ---------------------------------------------------------------------------


async def test_noop_always_clean() -> None:
    moderator = NoOpModerator()
    result = await moderator.moderate(b"any-bytes")
    assert result.decision == "clean"
    assert result.labels == {}
