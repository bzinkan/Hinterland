from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(_SCRIPTS))

import smoke_observation_w1 as observation_smoke  # noqa: E402


class _FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def test_w1_canary_measures_backend_transition_separately() -> None:
    clock = _FakeClock()
    clock.now = 20.0
    statuses = iter(("pending", "processing", "pilot_private"))

    status, elapsed_ms = observation_smoke._await_w1_pilot_private(
        lambda: next(statuses),
        transition_started_at=0.0,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )

    assert status == "pilot_private"
    assert elapsed_ms == 30_000
    assert clock.sleeps == [5.0, 5.0]


def test_w1_canary_enforces_deployed_server_slo() -> None:
    clock = _FakeClock()

    with pytest.raises(
        RuntimeError,
        match=r"within 150s: pending",
    ):
        observation_smoke._await_w1_pilot_private(
            lambda: "pending",
            transition_started_at=0.0,
            monotonic=clock.monotonic,
            sleep=clock.sleep,
        )

    assert clock.now == observation_smoke.W1_NOOP_SERVER_TRANSITION_SLO_SECONDS
    assert max(clock.sleeps) <= observation_smoke.W1_NOOP_STATUS_POLL_SECONDS


def test_w1_canary_rejects_forbidden_state_without_waiting() -> None:
    clock = _FakeClock()

    with pytest.raises(RuntimeError, match="forbidden child state clean"):
        observation_smoke._await_w1_pilot_private(
            lambda: "clean",
            transition_started_at=0.0,
            monotonic=clock.monotonic,
            sleep=clock.sleep,
        )

    assert clock.sleeps == []


def test_w1_canary_rejects_first_pilot_private_response_after_server_slo() -> None:
    clock = _FakeClock()
    clock.now = observation_smoke.W1_NOOP_SERVER_TRANSITION_SLO_SECONDS + 0.001

    with pytest.raises(
        RuntimeError,
        match=r"first returned pilot_private after the 150s server SLO",
    ):
        observation_smoke._await_w1_pilot_private(
            lambda: "pilot_private",
            transition_started_at=0.0,
            monotonic=clock.monotonic,
            sleep=clock.sleep,
        )

    assert clock.sleeps == []
