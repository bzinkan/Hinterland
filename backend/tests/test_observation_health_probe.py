from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from admin.observation_health_probe import probe


class _Mappings:
    def __init__(self, row: dict[str, int]) -> None:
        self._row = row

    def one(self) -> dict[str, int]:
        return self._row


class _Result:
    def __init__(self, row: dict[str, int]) -> None:
        self._row = row

    def mappings(self) -> _Mappings:
        return _Mappings(self._row)


@pytest.mark.asyncio
async def test_probe_emits_healthy_zero_snapshot() -> None:
    session = SimpleNamespace(
        execute=AsyncMock(
            return_value=_Result(
                {
                    "stale_moderation_outbox": 0,
                    "stale_pending_photos": 0,
                    "stale_dispatch_runs": 0,
                    "stale_rebuilds": 0,
                    "failed_rebuilds": 0,
                    "state_mismatches": 0,
                }
            )
        )
    )
    now = datetime(2026, 7, 9, 12, tzinfo=UTC)

    health = await probe(session, now=now)  # type: ignore[arg-type]

    assert health.healthy is True
    params = session.execute.await_args.args[1]
    assert params["moderation_cutoff"] == now - timedelta(minutes=10)
    assert params["pending_photo_cutoff"] == now - timedelta(hours=1)
    assert params["dispatch_cutoff"] == now - timedelta(minutes=10)
    assert params["rebuild_cutoff"] == now - timedelta(minutes=15)


@pytest.mark.asyncio
async def test_probe_surfaces_each_operational_failure_count() -> None:
    session = SimpleNamespace(
        execute=AsyncMock(
            return_value=_Result(
                {
                    "stale_moderation_outbox": 1,
                    "stale_pending_photos": 2,
                    "stale_dispatch_runs": 3,
                    "stale_rebuilds": 4,
                    "failed_rebuilds": 5,
                    "state_mismatches": 6,
                }
            )
        )
    )

    health = await probe(session)  # type: ignore[arg-type]

    assert health.healthy is False
    assert health.stale_moderation_outbox == 1
    assert health.stale_pending_photos == 2
    assert health.stale_dispatch_runs == 3
    assert health.stale_rebuilds == 4
    assert health.failed_rebuilds == 5
    assert health.state_mismatches == 6
