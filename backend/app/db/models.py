"""Postgres schema for the closed-beta production foundation."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

JsonDict = dict[str, object]


class TimestampMixin:
    """Common create/update timestamps for operational rows."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )


class User(TimestampMixin, Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("role in ('parent', 'teacher', 'kid')", name="ck_users_role"),
        UniqueConstraint("firebase_uid", name="uq_users_firebase_uid"),
        UniqueConstraint("entra_oid", name="uq_users_entra_oid"),
        Index("ix_users_entra_oid", "entra_oid"),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    firebase_uid: Mapped[str | None] = mapped_column(String(128))
    entra_oid: Mapped[str | None] = mapped_column(String(64))
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    display_name: Mapped[str] = mapped_column(String(80), nullable=False)
    age_band: Mapped[str | None] = mapped_column(String(16))
    parent_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    consent_granted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Group(TimestampMixin, Base):
    __tablename__ = "groups"
    __table_args__ = (UniqueConstraint("join_code", name="uq_groups_join_code"),)

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    join_code: Mapped[str] = mapped_column(String(6), nullable=False)
    owner_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Membership(TimestampMixin, Base):
    __tablename__ = "memberships"
    __table_args__ = (
        UniqueConstraint("group_id", "user_id", name="uq_memberships_group_user"),
        CheckConstraint("role in ('parent', 'teacher', 'kid')", name="ck_memberships_role"),
        CheckConstraint("observation_count >= 0", name="ck_memberships_observation_count"),
        CheckConstraint("dex_count >= 0", name="ck_memberships_dex_count"),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    group_id: Mapped[str] = mapped_column(ForeignKey("groups.id"), nullable=False)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    observation_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    dex_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rarest_tier: Mapped[str | None] = mapped_column(String(24))
    last_observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Photo(TimestampMixin, Base):
    __tablename__ = "photos"
    __table_args__ = (
        CheckConstraint(
            "status in ('pending', 'clean', 'quarantine', 'deleted')",
            name="ck_photos_status",
        ),
        CheckConstraint(
            "attachment_status in ('reserved', 'attached', 'deleted')",
            name="ck_photos_attachment_status",
        ),
        UniqueConstraint("bucket", "object_name", name="uq_photos_bucket_object"),
        UniqueConstraint("user_id", "submission_key", name="uq_photos_user_submission"),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    bucket: Mapped[str] = mapped_column(String(128), nullable=False)
    object_name: Mapped[str] = mapped_column(String(512), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    attachment_status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="reserved", server_default="reserved"
    )
    # Nullable only for migration-first compatibility with the previous API.
    # The W1 API always writes this value.
    submission_key: Mapped[str | None] = mapped_column(String(26))
    canonical_object_name: Mapped[str | None] = mapped_column(String(512))
    content_type: Mapped[str | None] = mapped_column(String(128))
    checksum: Mapped[str | None] = mapped_column(String(128))
    byte_count: Mapped[int | None] = mapped_column(Integer)
    width_px: Mapped[int | None] = mapped_column(Integer)
    height_px: Mapped[int | None] = mapped_column(Integer)
    sha256: Mapped[str | None] = mapped_column(String(64))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    moderated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Observation(TimestampMixin, Base):
    __tablename__ = "observations"
    __table_args__ = (
        Index("ix_observations_user_created", "user_id", "created_at"),
        Index("ix_observations_group_created", "group_id", "created_at"),
        Index("ix_observations_taxon", "taxon_id"),
        UniqueConstraint("photo_id", name="uq_observations_photo_id"),
        UniqueConstraint("user_id", "submission_key", name="uq_observations_user_submission"),
        CheckConstraint(
            "location_source in ('device_coarse', 'manual_coarse', 'none', 'legacy_coarsened')",
            name="ck_observations_location_source",
        ),
        CheckConstraint(
            "identification_source in ('catalog', 'cv', 'manual_text', 'unknown', 'legacy')",
            name="ck_observations_identification_source",
        ),
        CheckConstraint(
            "dispatch_status in ('pending', 'partial', 'complete', 'unverified')",
            name="ck_observations_dispatch_status",
        ),
        CheckConstraint(
            "moderation_source in ('none', 'noop', 'azure', 'adult')",
            name="ck_observations_moderation_source",
        ),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    group_id: Mapped[str] = mapped_column(ForeignKey("groups.id"), nullable=False)
    photo_id: Mapped[str] = mapped_column(ForeignKey("photos.id"), nullable=False)
    # Nullable only while one legacy API revision may still be running.
    submission_key: Mapped[str | None] = mapped_column(String(26))
    taxon_id: Mapped[int | None] = mapped_column(Integer)
    species_name: Mapped[str | None] = mapped_column(String(200))
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    geohash4: Mapped[str | None] = mapped_column(String(8))
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    location_source: Mapped[str] = mapped_column(
        String(24), nullable=False, default="legacy_coarsened", server_default="legacy_coarsened"
    )
    identification_source: Mapped[str] = mapped_column(
        String(24), nullable=False, default="legacy", server_default="legacy"
    )
    identification_revision: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    dispatch_status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="unverified", server_default="unverified"
    )
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    place_name: Mapped[str | None] = mapped_column(String(200))
    inat_observation_id: Mapped[int | None] = mapped_column(Integer)
    submitted_to_inat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Compatibility marker introduced before the revisioned identification
    # event. New identification changes rebuild derived state, but the column
    # remains part of the deployed mainline schema for one release.
    taxon_first_assigned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rewards: Mapped[list[JsonDict]] = mapped_column(JSONB, nullable=False, default=list)
    ecology_tags: Mapped[JsonDict] = mapped_column(JSONB, nullable=False, default=dict)
    # Azure Content Safety lifecycle state for this observation's photo.
    # The W1 lifecycle adds processing/pilot_private/failed while preserving
    # pending/clean/quarantine/rejected compatibility. Only `clean` rows can
    # ever reach the separately default-deny iNat submission path.
    moderation_status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="pending", server_default="pending"
    )
    # Per-category labels returned by the moderation provider. Empty dict
    # default; not queried, written by the moderation worker as evidence
    # for review + Risk 0002 closure.
    moderation_labels: Mapped[JsonDict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    moderation_source: Mapped[str] = mapped_column(
        String(24), nullable=False, default="none", server_default="none"
    )
    moderation_policy_version: Mapped[str | None] = mapped_column(String(64))


class ObservationIdempotency(TimestampMixin, Base):
    """Operation-scoped replay ledger for the two-step observation flow."""

    __tablename__ = "observation_idempotency"
    __table_args__ = (
        CheckConstraint(
            "operation in ('photo_presign', 'observation_create')",
            name="ck_observation_idempotency_operation",
        ),
    )

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    idempotency_key: Mapped[str] = mapped_column(String(26), primary_key=True)
    operation: Mapped[str] = mapped_column(String(32), primary_key=True)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(26), nullable=False)


class ModerationOutbox(TimestampMixin, Base):
    """Committed observation work awaiting relay to the moderation queue."""

    __tablename__ = "moderation_outbox"
    __table_args__ = (
        CheckConstraint(
            "status in ('pending', 'enqueued', 'processing', 'succeeded', 'failed', 'dlq')",
            name="ck_moderation_outbox_status",
        ),
        Index("ix_moderation_outbox_status_attempt", "status", "last_attempt_at"),
    )

    observation_id: Mapped[str] = mapped_column(
        ForeignKey("observations.id", ondelete="CASCADE"), primary_key=True
    )
    photo_id: Mapped[str] = mapped_column(ForeignKey("photos.id"), nullable=False)
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="pending", server_default="pending"
    )
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)


class ObservationHandlerRun(TimestampMixin, Base):
    """Durable, independently replayable execution record for one handler."""

    __tablename__ = "observation_handler_runs"
    __table_args__ = (
        CheckConstraint(
            "status in ('pending', 'running', 'succeeded', 'failed', 'blocked')",
            name="ck_observation_handler_runs_status",
        ),
        Index("ix_observation_handler_runs_status", "status", "updated_at"),
    )

    observation_id: Mapped[str] = mapped_column(
        ForeignKey("observations.id", ondelete="CASCADE"), primary_key=True
    )
    handler_name: Mapped[str] = mapped_column(String(80), primary_key=True)
    handler_version: Mapped[str] = mapped_column(String(32), nullable=False, default="1")
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="pending", server_default="pending"
    )
    state: Mapped[JsonDict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    rewards: Mapped[list[JsonDict]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    last_error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DerivedStateRebuild(TimestampMixin, Base):
    """Per-user authoritative derived-state rebuild requested by a correction."""

    __tablename__ = "derived_state_rebuilds"
    __table_args__ = (
        CheckConstraint(
            "status in ('queued', 'running', 'succeeded', 'failed')",
            name="ck_derived_state_rebuilds_status",
        ),
        Index("ix_derived_state_rebuilds_user_status", "user_id", "status"),
        Index(
            "uq_derived_state_rebuilds_active_user",
            "user_id",
            unique=True,
            postgresql_where=text("status in ('queued', 'running')"),
        ),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    trigger_observation_id: Mapped[str | None] = mapped_column(
        ForeignKey("observations.id", ondelete="SET NULL")
    )
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="queued", server_default="queued"
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    last_error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class InatSubmitOutbox(TimestampMixin, Base):
    """Transactional outbox row for the Azure Service Bus iNat-submit path.

    Written in the same SQLAlchemy transaction that flips an observation's
    `moderation_status` to `clean` (either by the moderation worker on the
    direct clean path or by the review-queue approve handler). After commit
    the producer enqueues `{ observation_id }` to the `inat-submit` Service
    Bus queue. If the send fails the row stays in `pending` and the 15-min
    replay job (`admin.inat_outbox_replay`) re-enqueues anything past a
    5-min grace window.

    Primary key is `observation_id` itself so the same observation cannot
    have two outbox rows -- duplicate inserts raise on the FK + PK and the
    caller treats the collision as "already enqueued, skip."

    State machine:
        pending  -> enqueued -> submitted
                              -> dlq        (terminal failure)
    """

    __tablename__ = "inat_submit_outbox"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'enqueued', 'submitted', 'dlq')",
            name="ck_inat_submit_outbox_status",
        ),
        Index("ix_inat_submit_outbox_status_attempt", "status", "last_attempt_at"),
    )

    observation_id: Mapped[str] = mapped_column(
        ForeignKey("observations.id", ondelete="CASCADE"), primary_key=True
    )
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="pending", server_default="pending"
    )
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)


class DexEntry(TimestampMixin, Base):
    __tablename__ = "dex_entries"
    __table_args__ = (
        UniqueConstraint("user_id", "taxon_id", name="uq_dex_entries_user_taxon"),
        Index("ix_dex_entries_group_taxon", "group_id", "taxon_id"),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    group_id: Mapped[str] = mapped_column(ForeignKey("groups.id"), nullable=False)
    taxon_id: Mapped[int] = mapped_column(Integer, nullable=False)
    species_name: Mapped[str | None] = mapped_column(String(200))
    first_observation_id: Mapped[str] = mapped_column(ForeignKey("observations.id"), nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ExpeditionContent(TimestampMixin, Base):
    __tablename__ = "expedition_content"
    __table_args__ = (UniqueConstraint("content_hash", name="uq_expedition_content_hash"),)

    id: Mapped[str] = mapped_column(String(120), primary_key=True)
    tier: Mapped[int] = mapped_column(Integer, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    body: Mapped[JsonDict] = mapped_column(JSONB, nullable=False)
    archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class ExpeditionProgress(TimestampMixin, Base):
    __tablename__ = "expedition_progress"
    __table_args__ = (
        UniqueConstraint("user_id", "expedition_id", name="uq_expedition_progress_user_exp"),
        Index(
            "uq_expedition_progress_user_focus",
            "user_id",
            unique=True,
            postgresql_where=text("focused_at IS NOT NULL"),
        ),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    group_id: Mapped[str] = mapped_column(ForeignKey("groups.id"), nullable=False)
    expedition_id: Mapped[str] = mapped_column(ForeignKey("expedition_content.id"), nullable=False)
    completed_steps: Mapped[JsonDict] = mapped_column(JSONB, nullable=False, default=dict)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    focused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ExpeditionObservationContribution(TimestampMixin, Base):
    """Idempotency gate tying one observation to one expedition step."""

    __tablename__ = "expedition_observation_contributions"

    observation_id: Mapped[str] = mapped_column(
        ForeignKey("observations.id", ondelete="CASCADE"), primary_key=True
    )
    expedition_id: Mapped[str] = mapped_column(
        ForeignKey("expedition_content.id", ondelete="CASCADE"), primary_key=True
    )
    step_id: Mapped[str] = mapped_column(String(120), nullable=False)


class ReviewQueueItem(TimestampMixin, Base):
    __tablename__ = "review_queue"
    __table_args__ = (
        CheckConstraint(
            "status in ('pending', 'approved', 'rejected')",
            name="ck_review_queue_status",
        ),
        Index("ix_review_queue_group_status", "group_id", "status"),
        UniqueConstraint("photo_id", name="uq_review_queue_photo_id"),
        UniqueConstraint("observation_id", name="uq_review_queue_observation_id"),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    group_id: Mapped[str] = mapped_column(ForeignKey("groups.id"), nullable=False)
    photo_id: Mapped[str] = mapped_column(ForeignKey("photos.id"), nullable=False)
    observation_id: Mapped[str | None] = mapped_column(ForeignKey("observations.id"))
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    reason: Mapped[str | None] = mapped_column(Text)
    reviewer_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class IngestRun(TimestampMixin, Base):
    __tablename__ = "ingest_runs"
    __table_args__ = (
        CheckConstraint(
            "status in ('running', 'succeeded', 'failed', 'cancelled')",
            name="ck_ingest_runs_status",
        ),
        UniqueConstraint("source", "source_run_id", name="uq_ingest_runs_source_run"),
        Index("ix_ingest_runs_source_status", "source", "status"),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    source_run_id: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="running")
    cursor: Mapped[JsonDict | None] = mapped_column(JSONB)
    checksum: Mapped[str | None] = mapped_column(String(128))
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class JobState(TimestampMixin, Base):
    __tablename__ = "job_state"

    name: Mapped[str] = mapped_column(String(80), primary_key=True)
    cursor: Mapped[JsonDict] = mapped_column(JSONB, nullable=False, default=dict)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)


class SpeciesCache(TimestampMixin, Base):
    __tablename__ = "species_cache"
    __table_args__ = (
        Index("ix_species_cache_common_name", "common_name"),
        Index("ix_species_cache_scientific_name", "scientific_name"),
    )

    taxon_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scientific_name: Mapped[str | None] = mapped_column(String(200))
    common_name: Mapped[str | None] = mapped_column(String(200))
    iconic_taxon: Mapped[str | None] = mapped_column(String(80))
    rank: Mapped[str | None] = mapped_column(String(80))
    ancestor_ids: Mapped[list[int]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    aliases: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    catalog_version: Mapped[str] = mapped_column(
        String(64), nullable=False, default="legacy", server_default="legacy"
    )
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_payload: Mapped[JsonDict] = mapped_column(JSONB, nullable=False, default=dict)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TaxonomyPack(TimestampMixin, Base):
    """Published catalog snapshot available for verified mobile import."""

    __tablename__ = "taxonomy_packs"
    __table_args__ = (
        UniqueConstraint("pack_id", "version", name="uq_taxonomy_packs_id_version"),
        Index("ix_taxonomy_packs_active_scope", "active", "scope"),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    pack_id: Mapped[str] = mapped_column(String(80), nullable=False)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    scope: Mapped[str] = mapped_column(String(80), nullable=False)
    checksum_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    taxon_count: Mapped[int] = mapped_column(Integer, nullable=False)
    bucket: Mapped[str] = mapped_column(String(128), nullable=False)
    object_name: Mapped[str] = mapped_column(String(512), nullable=False)
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )


class CvSuggestionCache(TimestampMixin, Base):
    """Post-clean CV result keyed by canonical bytes and explicit model."""

    __tablename__ = "cv_suggestion_cache"

    photo_sha256: Mapped[str] = mapped_column(String(64), primary_key=True)
    model_version: Mapped[str] = mapped_column(String(64), primary_key=True)
    suggestions: Mapped[list[JsonDict]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )


class GeoCache(TimestampMixin, Base):
    __tablename__ = "geo_cache"
    __table_args__ = (UniqueConstraint("rounded_lat", "rounded_lng", name="uq_geo_cache_lat_lng"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    rounded_lat: Mapped[str] = mapped_column(String(16), nullable=False)
    rounded_lng: Mapped[str] = mapped_column(String(16), nullable=False)
    place_name: Mapped[str] = mapped_column(String(200), nullable=False)
    source_payload: Mapped[JsonDict] = mapped_column(JSONB, nullable=False, default=dict)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RarityCache(TimestampMixin, Base):
    __tablename__ = "rarity_cache"
    __table_args__ = (
        UniqueConstraint("region_geohash", "taxon_id", name="uq_rarity_cache_region_taxon"),
        CheckConstraint(
            "tier in ('abundant', 'common', 'rare', 'epic', 'legendary', 'unrecorded')",
            name="ck_rarity_cache_tier",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    region_geohash: Mapped[str] = mapped_column(String(8), nullable=False)
    taxon_id: Mapped[int] = mapped_column(Integer, nullable=False)
    tier: Mapped[str] = mapped_column(String(24), nullable=False)
    observation_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    iconic_taxon: Mapped[str | None] = mapped_column(String(32))
    refreshed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class KidHandoffJti(TimestampMixin, Base):
    """Single-use ledger for kid handoff JWTs (Phase 6a).

    The ``POST /v1/auth/kid-exchange`` handler INSERTs a row keyed by the
    handoff token's ``jti`` claim. Duplicate-PK violation IS the single-use
    gate -- no application-level locking required. The ``ix_kid_handoff_jti_expires_at``
    index supports the background sweeper that purges rows past
    ``expires_at + 7 days``.
    """

    __tablename__ = "kid_handoff_jti"
    __table_args__ = (Index("ix_kid_handoff_jti_expires_at", "expires_at"),)

    jti: Mapped[str] = mapped_column(Text, primary_key=True)
    kid_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ParentConsentRecord(TimestampMixin, Base):
    """Durable parent-consent ledger for the COPPA / Designed-for-Families audit trail.

    Captured at the pre-signup `/consent` page. Today the page logs a
    structured event AND inserts a row here; the row is the audit-of-
    record (logs roll over at 30d). A parent's signup flow joins back to
    the newest row that matches the verified-email claim, populating
    ``linked_parent_user_id``. The kid-create flow joins similarly via
    ``linked_kid_user_id`` once the kid row is provisioned (post-pilot
    follow-up).

    Privacy posture: stores only what's needed to prove a parent saw and
    accepted the named policy version at a moment in time. We do NOT
    store raw IP or User-Agent strings; if request-IP / UA hashing is
    enabled at the edge, the hashes land here as opaque opaque
    identifiers (operator-managed salt). Lawyer-reviewed copy is still
    a production gate -- see ``docs/risks/0005-beta-launch-human-
    action-items.md`` and ``docs/privacy-policy-DRAFT.md``.
    """

    __tablename__ = "parent_consent_records"
    __table_args__ = (
        Index("ix_parent_consent_records_email", "parent_email"),
        Index("ix_parent_consent_records_policy_version", "policy_version"),
        Index("ix_parent_consent_records_recorded_at", "recorded_at"),
        Index("ix_parent_consent_records_linked_parent_user_id", "linked_parent_user_id"),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    parent_email: Mapped[str] = mapped_column(String(320), nullable=False)
    kid_display_name: Mapped[str | None] = mapped_column(String(80))
    policy_version: Mapped[str] = mapped_column(String(40), nullable=False)
    consent_text_version: Mapped[str | None] = mapped_column(String(40))
    source: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        server_default="web_consent",
    )
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    # SHA-256 hex (64) or future-proof for SHA3-256; nullable because the
    # /consent endpoint doesn't yet have a configured hashing salt.
    ip_hash: Mapped[str | None] = mapped_column(String(64))
    user_agent_hash: Mapped[str | None] = mapped_column(String(64))
    linked_parent_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
    )
    linked_kid_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
    )


# ---------------------------------------------------------------------------
# Sanctuary (Phase 2 -- see docs/sanctuary.md, PR #95/#96)
# ---------------------------------------------------------------------------
#
# Four per-user tables back the WorldHandler. None of these rows carry
# precise location, none participate in the leaderboard path (which stays on
# `memberships`), and the contribution table's PK on `observation_id` is the
# structural replay gate: a second dispatch of the same observation hits a
# PK collision, and the WorldHandler treats that as "skip every counter
# bump and element fire from this observation."


class SanctuaryZoneState(TimestampMixin, Base):
    """Per-user, per-zone Sanctuary observation count and depth tier."""

    __tablename__ = "sanctuary_zone_state"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "zone_id",
            name="uq_sanctuary_zone_state_user_zone",
        ),
        Index(
            "ix_sanctuary_zone_state_user_depth",
            "user_id",
            "depth_tier",
        ),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    zone_id: Mapped[str] = mapped_column(String(40), nullable=False)
    observation_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default="0",
    )
    depth_tier: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default="0",
    )
    # Bookkeeping pointers to the observations that crossed the wake-up and
    # most-recent-evolution thresholds. SET NULL on observation delete so
    # the zone state survives audit-driven observation deletion.
    first_unlocked_observation_id: Mapped[str | None] = mapped_column(
        ForeignKey("observations.id", ondelete="SET NULL"),
    )
    last_evolved_observation_id: Mapped[str | None] = mapped_column(
        ForeignKey("observations.id", ondelete="SET NULL"),
    )
    last_observed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
    )


class SanctuaryElement(TimestampMixin, Base):
    """Per-user record of a named Sanctuary element unlock.

    `element_type` is one of coarse / charismatic / relationship / surprise
    / signature. First-fire atomicity comes from the
    `(user_id, zone_id, element_id)` unique key plus `INSERT ... ON CONFLICT
    DO NOTHING` -- the Dex first-find idiom.
    """

    __tablename__ = "sanctuary_elements"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "zone_id",
            "element_id",
            name="uq_sanctuary_elements_user_zone_element",
        ),
        Index(
            "ix_sanctuary_elements_user_zone",
            "user_id",
            "zone_id",
        ),
        CheckConstraint(
            "element_type IN ('coarse','charismatic','relationship','surprise','signature')",
            name="ck_sanctuary_elements_element_type",
        ),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    zone_id: Mapped[str] = mapped_column(String(40), nullable=False)
    element_id: Mapped[str] = mapped_column(String(80), nullable=False)
    element_type: Mapped[str] = mapped_column(String(40), nullable=False)
    source_observation_id: Mapped[str | None] = mapped_column(
        ForeignKey("observations.id", ondelete="SET NULL"),
    )
    taxon_id: Mapped[int | None] = mapped_column(Integer)
    payload: Mapped[JsonDict] = mapped_column(JSONB, nullable=False, default=dict)
    unlocked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )


class SanctuaryObservationContribution(Base):
    """Idempotency gate for `WorldHandler` replay.

    PK is `observation_id` itself, so a second dispatch of the same
    observation raises a primary-key collision and the WorldHandler skips
    every counter bump + element fire from that observation. Write-once;
    no `updated_at` column.
    """

    __tablename__ = "sanctuary_observation_contributions"

    observation_id: Mapped[str] = mapped_column(
        ForeignKey("observations.id", ondelete="CASCADE"),
        primary_key=True,
    )
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    zone_id: Mapped[str] = mapped_column(String(40), nullable=False)
    taxon_id: Mapped[int | None] = mapped_column(Integer)
    iconic_taxon: Mapped[str | None] = mapped_column(String(80))
    element_ids: Mapped[list[JsonDict]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class SanctuaryEvent(Base):
    """Append-only Sanctuary event log row.

    `event_type` is one of world_unlock / world_evolution / relationship /
    surprise. Backs the per-zone journal / timeline and the on-submit
    celebration sequence. Immutable once written; no `updated_at` column.
    """

    __tablename__ = "sanctuary_events"
    __table_args__ = (
        Index(
            "ix_sanctuary_events_user_created_at",
            "user_id",
            "created_at",
        ),
        CheckConstraint(
            "event_type IN ('world_unlock','world_evolution','relationship','surprise')",
            name="ck_sanctuary_events_event_type",
        ),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    observation_id: Mapped[str | None] = mapped_column(
        ForeignKey("observations.id", ondelete="SET NULL"),
    )
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    zone_id: Mapped[str | None] = mapped_column(String(40))
    element_id: Mapped[str | None] = mapped_column(String(80))
    title: Mapped[str] = mapped_column(String(100), nullable=False)
    detail: Mapped[str | None] = mapped_column(String(240))
    payload: Mapped[JsonDict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
