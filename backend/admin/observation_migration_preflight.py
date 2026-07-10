"""Read-only preflight for the additive Observation W1 migration.

The report contains counts and bounded technical-ID samples only: no names,
photo URLs/bytes, coordinates, or auth material.  Reconciliations performed by
the migration (duplicate photo references, duplicate review rows, and negative
counters) require an acknowledgement token tied to the exact report snapshot.
Duplicate submission keys are hard blockers and cannot be acknowledged away.

Run before Alembic::

    python -m admin.observation_migration_preflight

If reconciliation is expected, inspect the first report and rerun with the
reported token::

    OBSERVATION_PREFLIGHT_ACK=<ack_token> \
      python -m admin.observation_migration_preflight
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import asdict, dataclass
from hashlib import sha256
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings

log = structlog.get_logger()


@dataclass(frozen=True)
class ObservationMigrationPreflight:
    duplicate_observation_photos: int
    duplicate_observation_photo_ids: tuple[str, ...]
    duplicate_review_photos: int
    duplicate_review_photo_ids: tuple[str, ...]
    duplicate_review_observations: int
    duplicate_review_observation_ids: tuple[str, ...]
    negative_membership_counters: int
    negative_membership_ids: tuple[str, ...]
    precise_location_rows: int
    duplicate_photo_submission_keys: int
    duplicate_photo_submission_key_samples: tuple[str, ...]
    duplicate_observation_submission_keys: int
    duplicate_observation_submission_key_samples: tuple[str, ...]

    @property
    def acknowledgement_required(self) -> bool:
        return any(
            (
                self.duplicate_observation_photos,
                self.duplicate_review_photos,
                self.duplicate_review_observations,
                self.negative_membership_counters,
            )
        )

    @property
    def hard_blocked(self) -> bool:
        return any(
            (
                self.duplicate_photo_submission_keys,
                self.duplicate_observation_submission_keys,
            )
        )

    @property
    def acknowledgement_token(self) -> str:
        payload = json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))
        return sha256(payload.encode("utf-8")).hexdigest()[:20]

    def public_report(self) -> dict[str, object]:
        return {
            **asdict(self),
            "acknowledgement_required": self.acknowledgement_required,
            "hard_blocked": self.hard_blocked,
            "ack_token": self.acknowledgement_token,
        }


_BASE_REPORT_SQL = text(
    """
    WITH duplicate_photo AS (
      SELECT photo_id
      FROM observations
      GROUP BY photo_id
      HAVING count(*) > 1
    ),
    duplicate_review_photo AS (
      SELECT photo_id
      FROM review_queue
      GROUP BY photo_id
      HAVING count(*) > 1
    ),
    duplicate_review_observation AS (
      SELECT observation_id
      FROM review_queue
      WHERE observation_id IS NOT NULL
      GROUP BY observation_id
      HAVING count(*) > 1
    ),
    negative_membership AS (
      SELECT id
      FROM memberships
      WHERE observation_count < 0 OR dex_count < 0
    ),
    precise_location AS (
      SELECT id
      FROM observations
      WHERE latitude IS NOT NULL OR longitude IS NOT NULL
    )
    SELECT
      (SELECT count(*) FROM duplicate_photo) AS duplicate_observation_photos,
      COALESCE(
        (SELECT json_agg(photo_id) FROM (
          SELECT photo_id FROM duplicate_photo ORDER BY photo_id LIMIT 20
        ) AS sample), '[]'::json
      ) AS duplicate_observation_photo_ids,
      (SELECT count(*) FROM duplicate_review_photo) AS duplicate_review_photos,
      COALESCE(
        (SELECT json_agg(photo_id) FROM (
          SELECT photo_id FROM duplicate_review_photo ORDER BY photo_id LIMIT 20
        ) AS sample), '[]'::json
      ) AS duplicate_review_photo_ids,
      (SELECT count(*) FROM duplicate_review_observation) AS duplicate_review_observations,
      COALESCE(
        (SELECT json_agg(observation_id) FROM (
          SELECT observation_id FROM duplicate_review_observation
          ORDER BY observation_id LIMIT 20
        ) AS sample), '[]'::json
      ) AS duplicate_review_observation_ids,
      (SELECT count(*) FROM negative_membership) AS negative_membership_counters,
      COALESCE(
        (SELECT json_agg(id) FROM (
          SELECT id FROM negative_membership ORDER BY id LIMIT 20
        ) AS sample), '[]'::json
      ) AS negative_membership_ids,
      (SELECT count(*) FROM precise_location) AS precise_location_rows
    """
)

_COLUMN_REPORT_SQL = text(
    """
    SELECT table_name, column_name
    FROM information_schema.columns
    WHERE table_schema = current_schema()
      AND (
        (table_name = 'photos' AND column_name = 'submission_key')
        OR (table_name = 'observations' AND column_name = 'submission_key')
      )
    """
)

_PHOTO_SUBMISSION_SQL = text(
    """
    WITH duplicate_key AS (
      SELECT submission_key
      FROM photos
      WHERE submission_key IS NOT NULL
      GROUP BY user_id, submission_key
      HAVING count(*) > 1
    )
    SELECT count(*) AS duplicate_count,
           COALESCE(
             (SELECT json_agg(submission_key) FROM (
               SELECT submission_key FROM duplicate_key
               ORDER BY submission_key LIMIT 20
             ) AS sample), '[]'::json
           ) AS samples
    FROM duplicate_key
    """
)

_OBSERVATION_SUBMISSION_SQL = text(
    """
    WITH duplicate_key AS (
      SELECT submission_key
      FROM observations
      WHERE submission_key IS NOT NULL
      GROUP BY user_id, submission_key
      HAVING count(*) > 1
    )
    SELECT count(*) AS duplicate_count,
           COALESCE(
             (SELECT json_agg(submission_key) FROM (
               SELECT submission_key FROM duplicate_key
               ORDER BY submission_key LIMIT 20
             ) AS sample), '[]'::json
           ) AS samples
    FROM duplicate_key
    """
)


def _tuple(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(str(item) for item in value)


async def build_report(session: AsyncSession) -> ObservationMigrationPreflight:
    base = (await session.execute(_BASE_REPORT_SQL)).mappings().one()
    columns = {
        (str(row[0]), str(row[1])) for row in (await session.execute(_COLUMN_REPORT_SQL)).all()
    }

    photo_count = 0
    photo_samples: tuple[str, ...] = ()
    if ("photos", "submission_key") in columns:
        photo = (await session.execute(_PHOTO_SUBMISSION_SQL)).mappings().one()
        photo_count = int(photo["duplicate_count"])
        photo_samples = _tuple(photo["samples"])

    observation_count = 0
    observation_samples: tuple[str, ...] = ()
    if ("observations", "submission_key") in columns:
        observation = (await session.execute(_OBSERVATION_SUBMISSION_SQL)).mappings().one()
        observation_count = int(observation["duplicate_count"])
        observation_samples = _tuple(observation["samples"])

    return ObservationMigrationPreflight(
        duplicate_observation_photos=int(base["duplicate_observation_photos"]),
        duplicate_observation_photo_ids=_tuple(base["duplicate_observation_photo_ids"]),
        duplicate_review_photos=int(base["duplicate_review_photos"]),
        duplicate_review_photo_ids=_tuple(base["duplicate_review_photo_ids"]),
        duplicate_review_observations=int(base["duplicate_review_observations"]),
        duplicate_review_observation_ids=_tuple(base["duplicate_review_observation_ids"]),
        negative_membership_counters=int(base["negative_membership_counters"]),
        negative_membership_ids=_tuple(base["negative_membership_ids"]),
        precise_location_rows=int(base["precise_location_rows"]),
        duplicate_photo_submission_keys=photo_count,
        duplicate_photo_submission_key_samples=photo_samples,
        duplicate_observation_submission_keys=observation_count,
        duplicate_observation_submission_key_samples=observation_samples,
    )


def validate_acknowledgement(
    report: ObservationMigrationPreflight,
    supplied_token: str,
) -> str | None:
    if report.hard_blocked:
        return "duplicate submission keys require manual reconciliation before migration"
    if report.acknowledgement_required and supplied_token != report.acknowledgement_token:
        return (
            "review the preflight report and set OBSERVATION_PREFLIGHT_ACK to its exact ack_token"
        )
    return None


async def main() -> None:
    settings = get_settings()
    engine = create_async_engine(settings.sqlalchemy_database_url)
    sessions: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine,
        expire_on_commit=False,
    )
    try:
        async with sessions() as session:
            report = await build_report(session)
        rendered = json.dumps(report.public_report(), sort_keys=True)
        print(rendered)
        error = validate_acknowledgement(
            report,
            os.environ.get("OBSERVATION_PREFLIGHT_ACK", "").strip(),
        )
        if error is not None:
            log.error(
                "observation.migration_preflight.blocked", reason=error, **report.public_report()
            )
            raise SystemExit(2)
        log.info("observation.migration_preflight.passed", **report.public_report())
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
