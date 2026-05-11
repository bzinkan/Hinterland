"""observations.dispatched_at column

Revision ID: 20260510_0002
Revises: 20260506_0001
Create Date: 2026-05-10

Adds `dispatched_at` to `observations` so the dispatcher replay task
(admin/dispatcher_replay.py) can find observations that crashed mid-
dispatch (or pre-date the column entirely) and re-run them.

NULL = dispatch hasn't been recorded as successful for this obs.
NOT NULL = dispatch finished and committed.

Backfill: deliberately leave existing rows at NULL. The replay task
will pick them up on its first run, which is intentional -- those
observations may legitimately have unprocessed handler side effects.
A future migration could backfill to NOW() if we decide retroactive
dispatch isn't desirable, but the safer default is "treat as
not-yet-dispatched and let the replay catch up."
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260510_0002"
down_revision = "20260506_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "observations",
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_observations_dispatched_at_null",
        "observations",
        ["created_at"],
        postgresql_where=sa.text("dispatched_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_observations_dispatched_at_null", table_name="observations")
    op.drop_column("observations", "dispatched_at")
