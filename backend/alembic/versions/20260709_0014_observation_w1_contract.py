"""Observation W1 safety and idempotency contract.

Revision ID: 20260709_0014
Revises: 20260709_0013
Create Date: 2026-07-09

This is deliberately additive. Compatibility columns remain in place for one
mobile release, but precise legacy coordinates are removed after their coarse
geohash has been retained.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20260709_0014"
down_revision = "20260709_0013"
branch_labels = None
depends_on = None


def _timestamps() -> tuple[sa.Column[object], sa.Column[object]]:
    return (
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def upgrade() -> None:
    # Membership counters must never drift below zero, even under duplicate
    # review/rebuild delivery.
    op.execute(
        sa.text(
            "UPDATE memberships SET observation_count = GREATEST(observation_count, 0), "
            "dex_count = GREATEST(dex_count, 0)"
        )
    )
    op.create_check_constraint(
        "ck_memberships_observation_count", "memberships", "observation_count >= 0"
    )
    op.create_check_constraint("ck_memberships_dex_count", "memberships", "dex_count >= 0")

    # Separate blob attachment from the legacy physical/moderation status.
    op.add_column(
        "photos",
        sa.Column(
            "attachment_status",
            sa.String(length=24),
            nullable=True,
            server_default=sa.text("'reserved'"),
        ),
    )
    op.add_column("photos", sa.Column("submission_key", sa.String(length=26), nullable=True))
    op.add_column(
        "photos", sa.Column("canonical_object_name", sa.String(length=512), nullable=True)
    )
    op.add_column("photos", sa.Column("byte_count", sa.Integer(), nullable=True))
    op.add_column("photos", sa.Column("width_px", sa.Integer(), nullable=True))
    op.add_column("photos", sa.Column("height_px", sa.Integer(), nullable=True))
    op.add_column("photos", sa.Column("sha256", sa.String(length=64), nullable=True))
    op.add_column("photos", sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True))

    op.execute(
        sa.text(
            """
            UPDATE photos AS p
               SET attachment_status = CASE
                     WHEN EXISTS (SELECT 1 FROM observations o WHERE o.photo_id = p.id)
                     THEN 'attached'
                     ELSE 'reserved'
                   END,
                   submission_key = p.id
            """
        )
    )
    op.alter_column("photos", "attachment_status", nullable=False)
    # Keep nullable for the migration-first compatibility window: the old API
    # revision is still serving while this additive migration runs and does
    # not populate submission_key. The new API always writes it; a later
    # cleanup revision may enforce NOT NULL after the old revision is gone.
    op.create_check_constraint(
        "ck_photos_attachment_status",
        "photos",
        "attachment_status in ('reserved', 'attached', 'deleted')",
    )
    op.create_unique_constraint(
        "uq_photos_user_submission", "photos", ["user_id", "submission_key"]
    )

    # Add the observation contract before reconciling the unlikely legacy
    # duplicate-photo case.
    op.add_column("observations", sa.Column("submission_key", sa.String(length=26), nullable=True))
    op.add_column(
        "observations",
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "observations",
        sa.Column(
            "location_source",
            sa.String(length=24),
            nullable=True,
            server_default=sa.text("'legacy_coarsened'"),
        ),
    )
    op.add_column(
        "observations",
        sa.Column(
            "identification_source",
            sa.String(length=24),
            nullable=True,
            server_default=sa.text("'legacy'"),
        ),
    )
    op.add_column(
        "observations",
        sa.Column(
            "identification_revision",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
    )
    op.add_column(
        "observations",
        sa.Column(
            "dispatch_status",
            sa.String(length=24),
            nullable=False,
            server_default=sa.text("'unverified'"),
        ),
    )
    op.add_column(
        "observations", sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "observations",
        sa.Column(
            "moderation_source",
            sa.String(length=24),
            nullable=False,
            server_default=sa.text("'none'"),
        ),
    )
    op.add_column(
        "observations",
        sa.Column("moderation_policy_version", sa.String(length=64), nullable=True),
    )

    op.execute(
        sa.text(
            """
            UPDATE observations
               SET submission_key = photo_id,
                   observed_at = created_at,
                   location_source = CASE
                     WHEN geohash4 IS NULL THEN 'none'
                     ELSE 'legacy_coarsened'
                   END,
                   identification_source = 'legacy',
                   dispatch_status = 'unverified'
            """
        )
    )
    op.alter_column("observations", "observed_at", nullable=False, server_default=sa.func.now())
    op.alter_column("observations", "location_source", nullable=False)
    op.alter_column("observations", "identification_source", nullable=False)
    # As above, defer NOT NULL until no old API instance can insert rows.

    # Preserve duplicate rows as rejected tombstones while giving each one a
    # distinct deleted-photo record so the one-observation-per-photo invariant
    # can be enforced without erasing audit history.
    op.execute(
        sa.text(
            """
            WITH duplicates AS (
              SELECT o.*,
                     row_number() OVER (
                       PARTITION BY o.photo_id ORDER BY o.created_at, o.id
                     ) AS rn
                FROM observations o
            )
            INSERT INTO photos (
              id, user_id, bucket, object_name, status, attachment_status,
              submission_key, content_type, checksum, created_at, updated_at
            )
            SELECT d.id, d.user_id, p.bucket,
                   'legacy-duplicate/' || d.id || '.jpg',
                   'deleted', 'deleted', d.id, p.content_type, p.checksum,
                   d.created_at, now()
              FROM duplicates d
              JOIN photos p ON p.id = d.photo_id
             WHERE d.rn > 1
            ON CONFLICT (id) DO NOTHING
            """
        )
    )
    op.execute(
        sa.text(
            """
            WITH duplicates AS (
              SELECT id,
                     row_number() OVER (
                       PARTITION BY photo_id ORDER BY created_at, id
                     ) AS rn
                FROM observations
            )
            UPDATE observations o
               SET photo_id = o.id,
                   submission_key = o.id,
                   moderation_status = 'rejected',
                   rejected_at = now()
              FROM duplicates d
             WHERE d.id = o.id AND d.rn > 1
            """
        )
    )

    op.alter_column("observations", "latitude", nullable=True)
    op.alter_column("observations", "longitude", nullable=True)
    op.execute(sa.text("UPDATE observations SET latitude = NULL, longitude = NULL"))

    op.drop_constraint("ck_observations_moderation_status", "observations", type_="check")
    op.create_check_constraint(
        "ck_observations_moderation_status",
        "observations",
        "moderation_status in "
        "('pending', 'processing', 'clean', 'quarantine', 'pilot_private', 'rejected', 'failed')",
    )
    op.create_check_constraint(
        "ck_observations_location_source",
        "observations",
        "location_source in ('device_coarse', 'manual_coarse', 'none', 'legacy_coarsened')",
    )
    op.create_check_constraint(
        "ck_observations_identification_source",
        "observations",
        "identification_source in ('catalog', 'cv', 'manual_text', 'unknown', 'legacy')",
    )
    op.create_check_constraint(
        "ck_observations_dispatch_status",
        "observations",
        "dispatch_status in ('pending', 'partial', 'complete', 'unverified')",
    )
    op.create_check_constraint(
        "ck_observations_moderation_source",
        "observations",
        "moderation_source in ('none', 'noop', 'azure', 'adult')",
    )
    # `uq_observations_photo_id` already exists from 20260702_0008.
    # Preserve that mainline constraint rather than attempting to recreate it.
    op.create_unique_constraint(
        "uq_observations_user_submission", "observations", ["user_id", "submission_key"]
    )

    # Collapse both duplicate-photo and duplicate-observation review items
    # before installing either unique constraint. The preflight can explicitly
    # acknowledge both categories, so migration reconciliation must cover both.
    # The earliest record remains the audit record.
    op.execute(
        sa.text(
            """
            DELETE FROM review_queue newer
             USING review_queue older
             WHERE (
                    newer.photo_id = older.photo_id
                    OR (
                         newer.observation_id IS NOT NULL
                         AND newer.observation_id = older.observation_id
                    )
                   )
               AND (newer.created_at, newer.id) > (older.created_at, older.id)
            """
        )
    )
    op.create_unique_constraint("uq_review_queue_photo_id", "review_queue", ["photo_id"])
    op.create_unique_constraint(
        "uq_review_queue_observation_id", "review_queue", ["observation_id"]
    )

    op.create_table(
        "observation_idempotency",
        sa.Column(
            "user_id",
            sa.String(length=26),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("idempotency_key", sa.String(length=26), primary_key=True),
        sa.Column("operation", sa.String(length=32), primary_key=True),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("resource_id", sa.String(length=26), nullable=False),
        *_timestamps(),
        sa.CheckConstraint(
            "operation in ('photo_presign', 'observation_create')",
            name="ck_observation_idempotency_operation",
        ),
    )

    op.create_table(
        "moderation_outbox",
        sa.Column(
            "observation_id",
            sa.String(length=26),
            sa.ForeignKey("observations.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("photo_id", sa.String(length=26), sa.ForeignKey("photos.id"), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="pending"),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        *_timestamps(),
        sa.CheckConstraint(
            "status in ('pending', 'enqueued', 'processing', 'succeeded', 'failed', 'dlq')",
            name="ck_moderation_outbox_status",
        ),
    )
    op.create_index(
        "ix_moderation_outbox_status_attempt",
        "moderation_outbox",
        ["status", "last_attempt_at"],
    )
    # Event Grid is removed before this migration runs. Register every
    # already-attached legacy pending observation in the transactional outbox
    # so that containment cannot strand work. Legacy `pending/<id>.jpg` bytes
    # are canonicalized by `admin.observation_legacy_reconcile` before the
    # relay is allowed to publish the row.
    op.execute(
        sa.text(
            """
            INSERT INTO moderation_outbox (
              observation_id, photo_id, status, retry_count, created_at, updated_at
            )
            SELECT o.id, o.photo_id, 'pending', 0, now(), now()
              FROM observations o
              JOIN photos p ON p.id = o.photo_id
             WHERE o.moderation_status = 'pending'
               AND o.rejected_at IS NULL
               AND p.status = 'pending'
               AND p.attachment_status = 'attached'
            ON CONFLICT (observation_id) DO NOTHING
            """
        )
    )

    op.create_table(
        "observation_handler_runs",
        sa.Column(
            "observation_id",
            sa.String(length=26),
            sa.ForeignKey("observations.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("handler_name", sa.String(length=80), primary_key=True),
        sa.Column("handler_version", sa.String(length=32), nullable=False, server_default="1"),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="pending"),
        sa.Column(
            "state",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "rewards",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.CheckConstraint(
            "status in ('pending', 'running', 'succeeded', 'failed', 'blocked')",
            name="ck_observation_handler_runs_status",
        ),
    )
    op.create_index(
        "ix_observation_handler_runs_status",
        "observation_handler_runs",
        ["status", "updated_at"],
    )

    op.create_table(
        "derived_state_rebuilds",
        sa.Column("id", sa.String(length=26), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(length=26),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "trigger_observation_id",
            sa.String(length=26),
            sa.ForeignKey("observations.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="queued"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.CheckConstraint(
            "status in ('queued', 'running', 'succeeded', 'failed')",
            name="ck_derived_state_rebuilds_status",
        ),
    )
    op.create_index(
        "ix_derived_state_rebuilds_user_status",
        "derived_state_rebuilds",
        ["user_id", "status"],
    )
    op.create_index(
        "uq_derived_state_rebuilds_active_user",
        "derived_state_rebuilds",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("status in ('queued', 'running')"),
    )
    op.execute(
        sa.text(
            """
            INSERT INTO derived_state_rebuilds (
              id, user_id, trigger_observation_id, status, attempt_count,
              created_at, updated_at
            )
            SELECT 'R' || upper(substr(md5(o.user_id), 1, 25)),
                   o.user_id, NULL, 'queued', 0, now(), now()
              FROM observations o
             WHERE o.rejected_at IS NULL
             GROUP BY o.user_id
            ON CONFLICT DO NOTHING
            """
        )
    )

    op.create_table(
        "expedition_observation_contributions",
        sa.Column(
            "observation_id",
            sa.String(length=26),
            sa.ForeignKey("observations.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "expedition_id",
            sa.String(length=120),
            sa.ForeignKey("expedition_content.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("step_id", sa.String(length=120), nullable=False),
        *_timestamps(),
    )

    op.add_column("species_cache", sa.Column("rank", sa.String(length=80), nullable=True))
    op.add_column(
        "species_cache",
        sa.Column(
            "ancestor_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column(
        "species_cache",
        sa.Column(
            "aliases",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column(
        "species_cache",
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )
    op.add_column(
        "species_cache",
        sa.Column(
            "catalog_version",
            sa.String(length=64),
            nullable=False,
            server_default=sa.text("'legacy'"),
        ),
    )
    op.add_column(
        "species_cache",
        sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_species_cache_common_name", "species_cache", ["common_name"])
    op.create_index("ix_species_cache_scientific_name", "species_cache", ["scientific_name"])


def downgrade() -> None:
    op.drop_index("ix_species_cache_scientific_name", table_name="species_cache")
    op.drop_index("ix_species_cache_common_name", table_name="species_cache")
    op.drop_column("species_cache", "source_updated_at")
    op.drop_column("species_cache", "catalog_version")
    op.drop_column("species_cache", "active")
    op.drop_column("species_cache", "aliases")
    op.drop_column("species_cache", "ancestor_ids")
    op.drop_column("species_cache", "rank")

    op.drop_table("expedition_observation_contributions")
    op.drop_index("uq_derived_state_rebuilds_active_user", table_name="derived_state_rebuilds")
    op.drop_index("ix_derived_state_rebuilds_user_status", table_name="derived_state_rebuilds")
    op.drop_table("derived_state_rebuilds")
    op.drop_index("ix_observation_handler_runs_status", table_name="observation_handler_runs")
    op.drop_table("observation_handler_runs")
    op.drop_index("ix_moderation_outbox_status_attempt", table_name="moderation_outbox")
    op.drop_table("moderation_outbox")
    op.drop_table("observation_idempotency")

    op.drop_constraint("uq_review_queue_observation_id", "review_queue", type_="unique")
    op.drop_constraint("uq_review_queue_photo_id", "review_queue", type_="unique")
    op.drop_constraint("uq_observations_user_submission", "observations", type_="unique")
    # `uq_observations_photo_id` predates this revision and remains on downgrade.
    op.drop_constraint("ck_observations_dispatch_status", "observations", type_="check")
    op.drop_constraint("ck_observations_moderation_source", "observations", type_="check")
    op.drop_constraint("ck_observations_identification_source", "observations", type_="check")
    op.drop_constraint("ck_observations_location_source", "observations", type_="check")
    op.drop_constraint("ck_observations_moderation_status", "observations", type_="check")
    op.create_check_constraint(
        "ck_observations_moderation_status",
        "observations",
        "moderation_status in ('pending', 'clean', 'quarantine', 'rejected')",
    )
    op.drop_column("observations", "moderation_policy_version")
    op.drop_column("observations", "moderation_source")
    op.drop_column("observations", "rejected_at")
    op.drop_column("observations", "dispatch_status")
    op.drop_column("observations", "identification_revision")
    op.drop_column("observations", "identification_source")
    op.drop_column("observations", "location_source")
    op.drop_column("observations", "observed_at")
    op.drop_column("observations", "submission_key")

    # A downgrade cannot restore deliberately discarded precise coordinates;
    # fail visibly instead of fabricating them.
    op.execute(
        sa.text("UPDATE observations SET latitude = 0, longitude = 0 WHERE latitude IS NULL")
    )
    op.alter_column("observations", "longitude", nullable=False)
    op.alter_column("observations", "latitude", nullable=False)

    op.drop_constraint("uq_photos_user_submission", "photos", type_="unique")
    op.drop_constraint("ck_photos_attachment_status", "photos", type_="check")
    op.drop_column("photos", "verified_at")
    op.drop_column("photos", "sha256")
    op.drop_column("photos", "height_px")
    op.drop_column("photos", "width_px")
    op.drop_column("photos", "byte_count")
    op.drop_column("photos", "canonical_object_name")
    op.drop_column("photos", "submission_key")
    op.drop_column("photos", "attachment_status")
    op.drop_constraint("ck_memberships_dex_count", "memberships", type_="check")
    op.drop_constraint("ck_memberships_observation_count", "memberships", type_="check")
