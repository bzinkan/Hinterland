"""Shared parsing for `expedition_progress.completed_steps` values.

Each key in `completed_steps` is a step id; the value records when (and
by which observation) the step completed. Two formats exist:

- Current: ``{"completed_at": <iso string>, "observation_id": <ulid>}``,
  written by the ExpeditionHandler since the per-observation replay gate
  landed.
- Legacy: a plain ISO-8601 string. These exist in rows written before
  this change and carry no observation id.

`parse_step_completion` normalizes both so readers (the handler's replay
gate, the `/v1/expeditions/me` step detail) never branch on shape.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StepCompletion:
    completed_at: str | None
    observation_id: str | None


def parse_step_completion(value: object) -> StepCompletion:
    """Parse one `completed_steps` value, tolerating both formats.

    Legacy string values exist in rows written before this change --
    they parse as a completion with an unknown observation. Anything
    that is neither a string nor a dict parses as fully unknown.
    """
    if isinstance(value, str):
        return StepCompletion(completed_at=value, observation_id=None)
    if isinstance(value, dict):
        completed_at = value.get("completed_at")
        observation_id = value.get("observation_id")
        return StepCompletion(
            completed_at=completed_at if isinstance(completed_at, str) else None,
            observation_id=observation_id if isinstance(observation_id, str) else None,
        )
    return StepCompletion(completed_at=None, observation_id=None)
