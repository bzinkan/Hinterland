"""sanctuary state tables -- per-user zone counters, elements, contributions, events

Revision ID: 20260603_0005
Revises: 20260603_0004
Create Date: 2026-06-03

Introduces the four Sanctuary persistence tables that back the Phase 2
``WorldHandler`` (per ``docs/sanctuary.md`` sections 9 and 12). All state is
per-user and Postgres-backed; there are no DynamoDB-style ``WORLD#`` /
``ZONE#`` partition keys (ADR 0005).

Tables:

* ``sanctuary_zone_state`` -- per-user, per-zone observation counter and
  current depth tier. Unique on ``(user_id, zone_id)``. Counter bumps land
  via atomic ``UPDATE ... RETURNING`` from the WorldHandler, mirroring the
  leaderboard-counter invariant on ``memberships`` (those counters stay on
  membership rows; this table is independent of the leaderboard path).
* ``sanctuary_elements`` -- per-user record of which named Sanctuary
  elements (zone wake-ups, charismatic species, relationship moments,
  surprises, signatures) have fired. Unique on
  ``(user_id, zone_id, element_id)`` so first-fire is atomic via
  ``INSERT ... ON CONFLICT DO NOTHING`` -- the Dex first-find pattern.
* ``sanctuary_observation_contributions`` -- one row per observation that
  contributed to Sanctuary state. PK is ``observation_id`` itself (FK to
  ``observations.id``), giving natural replay idempotency on the
  dispatcher hot path. A second dispatch of the same observation raises a
  PK collision; the WorldHandler treats that as "skip every counter bump
  and element fire from this observation."
* ``sanctuary_events`` -- append-only audit trail of zone unlocks /
  evolutions / relationship moments / surprises shown to the kid. Soft
  link to ``observations.id`` (nullable, SET NULL on delete) so the audit
  row survives observation deletion.

No precise location, no leaderboard counters, no membership references
live on these tables. No data backfill. Downgrade drops all four tables
in reverse creation order.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20260603_0005"
down_revision = "20260603_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sanctuary_zone_state",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("user_id", sa.String(length=26), nullable=False),
        sa.Column("zone_id", sa.String(length=40), nullable=False),
        sa.Column(
            "observation_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "depth_tier",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "first_unlocked_observation_id",
            sa.String(length=26),
            nullable=True,
        ),
        sa.Column(
            "last_evolved_observation_id",
            sa.String(length=26),
            nullable=True,
        ),
        sa.Column(
            "last_observed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["first_unlocked_observation_id"],
            ["observations.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["last_evolved_observation_id"],
            ["observations.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "zone_id",
            name="uq_sanctuary_zone_state_user_zone",
        ),
    )
    op.create_index(
        "ix_sanctuary_zone_state_user_depth",
        "sanctuary_zone_state",
        ["user_id", "depth_tier"],
    )

    op.create_table(
        "sanctuary_elements",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("user_id", sa.String(length=26), nullable=False),
        sa.Column("zone_id", sa.String(length=40), nullable=False),
        sa.Column("element_id", sa.String(length=80), nullable=False),
        sa.Column("element_type", sa.String(length=40), nullable=False),
        sa.Column(
            "source_observation_id",
            sa.String(length=26),
            nullable=True,
        ),
        sa.Column("taxon_id", sa.Integer(), nullable=True),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "unlocked_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "element_type IN ('coarse','charismatic','relationship','surprise','signature')",
            name="ck_sanctuary_elements_element_type",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_observation_id"],
            ["observations.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "zone_id",
            "element_id",
            name="uq_sanctuary_elements_user_zone_element",
        ),
    )
    op.create_index(
        "ix_sanctuary_elements_user_zone",
        "sanctuary_elements",
        ["user_id", "zone_id"],
    )

    op.create_table(
        "sanctuary_observation_contributions",
        sa.Column("observation_id", sa.String(length=26), nullable=False),
        sa.Column("user_id", sa.String(length=26), nullable=False),
        sa.Column("zone_id", sa.String(length=40), nullable=False),
        sa.Column("taxon_id", sa.Integer(), nullable=True),
        sa.Column("iconic_taxon", sa.String(length=80), nullable=True),
        sa.Column(
            "element_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["observation_id"],
            ["observations.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("observation_id"),
    )

    op.create_table(
        "sanctuary_events",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("user_id", sa.String(length=26), nullable=False),
        sa.Column(
            "observation_id",
            sa.String(length=26),
            nullable=True,
        ),
        sa.Column("event_type", sa.String(length=40), nullable=False),
        sa.Column("zone_id", sa.String(length=40), nullable=True),
        sa.Column("element_id", sa.String(length=80), nullable=True),
        sa.Column("title", sa.String(length=100), nullable=False),
        sa.Column("detail", sa.String(length=240), nullable=True),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "event_type IN ('world_unlock','world_evolution','relationship','surprise')",
            name="ck_sanctuary_events_event_type",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["observation_id"],
            ["observations.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_sanctuary_events_user_created_at",
        "sanctuary_events",
        ["user_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_sanctuary_events_user_created_at",
        table_name="sanctuary_events",
    )
    op.drop_table("sanctuary_events")

    op.drop_table("sanctuary_observation_contributions")

    op.drop_index(
        "ix_sanctuary_elements_user_zone",
        table_name="sanctuary_elements",
    )
    op.drop_table("sanctuary_elements")

    op.drop_index(
        "ix_sanctuary_zone_state_user_depth",
        table_name="sanctuary_zone_state",
    )
    op.drop_table("sanctuary_zone_state")
