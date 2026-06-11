"""Tests for the shared completed_steps value parser."""

from __future__ import annotations

from app.services.expedition_progress import StepCompletion, parse_step_completion


def test_legacy_string_parses_as_completed_at_only() -> None:
    parsed = parse_step_completion("2026-05-10T12:00:00+00:00")
    assert parsed == StepCompletion(
        completed_at="2026-05-10T12:00:00+00:00",
        observation_id=None,
    )


def test_dict_format_parses_both_fields() -> None:
    parsed = parse_step_completion(
        {
            "completed_at": "2026-05-10T12:00:00+00:00",
            "observation_id": "01J0OBSID0000000000000ULID",
        }
    )
    assert parsed.completed_at == "2026-05-10T12:00:00+00:00"
    assert parsed.observation_id == "01J0OBSID0000000000000ULID"


def test_dict_drops_missing_or_non_string_values() -> None:
    assert parse_step_completion({}) == StepCompletion(None, None)
    assert parse_step_completion({"completed_at": 5, "observation_id": ["x"]}) == StepCompletion(
        None, None
    )
    assert parse_step_completion({"observation_id": "obs-1"}) == StepCompletion(None, "obs-1")


def test_garbage_values_parse_as_unknown() -> None:
    for garbage in (None, 42, 3.14, True, ["2026-05-10T12:00:00+00:00"]):
        assert parse_step_completion(garbage) == StepCompletion(None, None)
