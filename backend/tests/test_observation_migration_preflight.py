from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from admin.observation_migration_preflight import (
    ObservationMigrationPreflight,
    build_report,
    validate_acknowledgement,
)


class _Mappings:
    def __init__(self, row: dict[str, object]) -> None:
        self._row = row

    def one(self) -> dict[str, object]:
        return self._row


class _Result:
    def __init__(
        self,
        *,
        row: dict[str, object] | None = None,
        rows: list[tuple[str, str]] | None = None,
    ) -> None:
        self._row = row or {}
        self._rows = rows or []

    def mappings(self) -> _Mappings:
        return _Mappings(self._row)

    def all(self) -> list[tuple[str, str]]:
        return self._rows


def _base(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "duplicate_observation_photos": 0,
        "duplicate_observation_photo_ids": [],
        "duplicate_review_photos": 0,
        "duplicate_review_photo_ids": [],
        "duplicate_review_observations": 0,
        "duplicate_review_observation_ids": [],
        "negative_membership_counters": 0,
        "negative_membership_ids": [],
        "precise_location_rows": 0,
    }
    values.update(overrides)
    return values


@pytest.mark.asyncio
async def test_legacy_schema_clean_report_requires_no_acknowledgement() -> None:
    session = SimpleNamespace(
        execute=AsyncMock(
            side_effect=[
                _Result(row=_base(precise_location_rows=12)),
                _Result(rows=[]),
            ]
        )
    )

    report = await build_report(session)  # type: ignore[arg-type]

    assert report.precise_location_rows == 12
    assert report.acknowledgement_required is False
    assert report.hard_blocked is False
    assert validate_acknowledgement(report, "") is None


@pytest.mark.asyncio
async def test_report_checks_optional_submission_columns_when_present() -> None:
    session = SimpleNamespace(
        execute=AsyncMock(
            side_effect=[
                _Result(
                    row=_base(
                        duplicate_observation_photos=2,
                        duplicate_observation_photo_ids=["photo-a", "photo-b"],
                    )
                ),
                _Result(
                    rows=[
                        ("photos", "submission_key"),
                        ("observations", "submission_key"),
                    ]
                ),
                _Result(row={"duplicate_count": 1, "samples": ["photo-key"]}),
                _Result(row={"duplicate_count": 1, "samples": ["observation-key"]}),
            ]
        )
    )

    report = await build_report(session)  # type: ignore[arg-type]

    assert report.duplicate_observation_photo_ids == ("photo-a", "photo-b")
    assert report.duplicate_photo_submission_keys == 1
    assert report.duplicate_observation_submission_keys == 1
    assert report.hard_blocked is True
    assert "manual reconciliation" in (validate_acknowledgement(report, "anything") or "")


def test_acknowledgement_token_is_tied_to_exact_report() -> None:
    report = ObservationMigrationPreflight(
        duplicate_observation_photos=1,
        duplicate_observation_photo_ids=("photo-a",),
        duplicate_review_photos=0,
        duplicate_review_photo_ids=(),
        duplicate_review_observations=0,
        duplicate_review_observation_ids=(),
        negative_membership_counters=0,
        negative_membership_ids=(),
        precise_location_rows=3,
        duplicate_photo_submission_keys=0,
        duplicate_photo_submission_key_samples=(),
        duplicate_observation_submission_keys=0,
        duplicate_observation_submission_key_samples=(),
    )

    assert report.acknowledgement_required is True
    assert validate_acknowledgement(report, "") is not None
    assert validate_acknowledgement(report, report.acknowledgement_token) is None

    changed = ObservationMigrationPreflight(
        **{
            **report.__dict__,
            "negative_membership_counters": 1,
            "negative_membership_ids": ("membership-a",),
        }
    )
    assert changed.acknowledgement_token != report.acknowledgement_token
    assert validate_acknowledgement(changed, report.acknowledgement_token) is not None


def test_migration_reconciles_every_acknowledgeable_review_duplicate() -> None:
    migration = (
        Path(__file__).parents[1] / "alembic/versions/20260709_0014_observation_w1_contract.py"
    ).read_text(encoding="utf-8")

    assert "newer.photo_id = older.photo_id" in migration
    assert "newer.observation_id = older.observation_id" in migration
    assert migration.index("newer.observation_id = older.observation_id") < migration.index(
        'op.create_unique_constraint(\n        "uq_review_queue_observation_id"'
    )
