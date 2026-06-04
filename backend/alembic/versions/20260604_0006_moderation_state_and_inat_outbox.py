"""moderation state on observations + inat_submit_outbox table

Revision ID: 20260604_0006
Revises: 20260603_0005
Create Date: 2026-06-04

Adds two related pieces of state needed to close Risk 0002 (async
workers production wiring under ADR 0010 Azure):

1. Two columns on ``observations``:
   * ``moderation_status`` -- enum-ish String tracking the Azure
     Content Safety lifecycle on the observation: ``pending`` (default,
     observation row exists but the photo has not been moderated yet),
     ``clean`` (Content Safety cleared the photo), ``quarantine``
     (Content Safety flagged the photo and an adult review_queue row
     was inserted), ``rejected`` (the adult reviewer rejected the
     photo, or the photo was permanently flagged). Source of truth for
     "should this observation be eligible for iNat submission?" --
     only ``clean`` rows are picked up by the iNat submit worker.
     CHECK constraint pins the vocabulary; default ``pending`` lets
     existing rows backfill cleanly (legacy rows pre-Phase-8 wiring
     are treated as still-needing-moderation, which is the safe
     default; a follow-up backfill can flip them to ``clean`` after
     manual review).
   * ``moderation_labels`` -- JSONB capturing the per-category scores
     returned by Azure Content Safety (or any future moderation
     provider). Empty dict default. Used for reporting + Risk 0002
     closure evidence ("here are the labels that made us quarantine
     observation X"). Not used at query time today; the column exists
     so the moderation worker has somewhere to durably write its
     decision context.

2. New table ``inat_submit_outbox`` -- the transactional outbox row
   that backs the Azure Service Bus iNat-submit path. The moderation
   worker (clean path) and the review_queue approve handler each
   insert one row in the same SQLAlchemy transaction that flips
   ``observations.moderation_status`` to ``clean``, commit, and only
   then enqueue ``{ observation_id }`` to Service Bus. On Service Bus
   send failure, the outbox row stays in ``pending`` and a 15-min
   replay job (``backend/admin/inat_outbox_replay.py``) re-enqueues
   anything past a 5-min grace window. Primary key = ``observation_id``
   (FK -> observations.id CASCADE) gives us natural idempotency: the
   same observation cannot have two outbox rows. State machine:

       pending  -- written by moderation/review-approve in-transaction
       enqueued -- enqueue_to_service_bus() returned 2xx
       submitted -- inat_submit_consumer dequeued and successfully
                    pushed to iNat (also sets observations.inat_observation_id)
       dlq      -- consumer terminal failure; manual intervention only

   ``retry_count`` + ``last_attempt_at`` + ``last_error`` (Text)
   support the replay job's debug + cap logic. CHECK pins the status
   vocabulary. Index on (status, last_attempt_at) so the replay query
   stays fast at any volume.

No backfill of historical observations. Existing observations get
``moderation_status='pending'`` by default; the operator can flip them
in bulk later if desired. The outbox table starts empty -- there is
no historical iNat-submission queue to repopulate.

Downgrade drops the outbox table and the two columns in reverse
order. Safe because no production traffic depends on either yet
(Risk 0002 production wiring follows in subsequent PRs).
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20260604_0006"
down_revision = "20260603_0005"
branch_labels = None
depends_on = None


_MODERATION_STATUS_VALUES = ("pending", "clean", "quarantine", "rejected")
_OUTBOX_STATUS_VALUES = ("pending", "enqueued", "submitted", "dlq")


def upgrade() -> None:
    op.add_column(
        "observations",
        sa.Column(
            "moderation_status",
            sa.String(length=24),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
    )
    op.add_column(
        "observations",
        sa.Column(
            "moderation_labels",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.create_check_constraint(
        "ck_observations_moderation_status",
        "observations",
        sa.column("moderation_status").in_(_MODERATION_STATUS_VALUES),
    )

    op.create_table(
        "inat_submit_outbox",
        sa.Column(
            "observation_id",
            sa.String(length=26),
            sa.ForeignKey("observations.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "status",
            sa.String(length=24),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
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
        sa.CheckConstraint(
            sa.column("status").in_(_OUTBOX_STATUS_VALUES),
            name="ck_inat_submit_outbox_status",
        ),
    )
    op.create_index(
        "ix_inat_submit_outbox_status_attempt",
        "inat_submit_outbox",
        ["status", "last_attempt_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_inat_submit_outbox_status_attempt", table_name="inat_submit_outbox")
    op.drop_table("inat_submit_outbox")
    op.drop_constraint("ck_observations_moderation_status", "observations", type_="check")
    op.drop_column("observations", "moderation_labels")
    op.drop_column("observations", "moderation_status")
