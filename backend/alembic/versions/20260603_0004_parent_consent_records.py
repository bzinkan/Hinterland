"""parent consent records -- durable COPPA audit ledger

Revision ID: 20260603_0004
Revises: 20260602_0003
Create Date: 2026-06-03

Before the W1 adult-supervised kid pilot (per
``docs/one-week-kid-pilot-checklist.md``), parent consent must be in
Postgres -- not just structured Cloud Logging -- so the audit-of-record
survives log retention rollover (30d on the existing Cloud Logging
sink, indefinite-but-not-queryable in Container Apps log archival).

The endpoint ``POST /v1/auth/consent`` is public-unauthenticated and
fires before a parent has any user row, so the consent ledger lives in
its own table keyed by email + recorded_at; the parent-signup flow
joins back via the email claim once a Firebase / Entra token is
verified.

Privacy: we store only what's needed to prove a parent saw and
accepted a policy version at a point in time. No raw IP or UA. The
``ip_hash`` / ``user_agent_hash`` columns are nullable scratch space
for a future operator-managed-salt scheme; today the endpoint writes
NULL there.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260603_0004"
down_revision = "20260602_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "parent_consent_records",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("parent_email", sa.String(length=320), nullable=False),
        sa.Column("kid_display_name", sa.String(length=80), nullable=True),
        sa.Column("policy_version", sa.String(length=40), nullable=False),
        sa.Column("consent_text_version", sa.String(length=40), nullable=True),
        sa.Column(
            "source",
            sa.String(length=32),
            nullable=False,
            server_default="web_consent",
        ),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column("ip_hash", sa.String(length=64), nullable=True),
        sa.Column("user_agent_hash", sa.String(length=64), nullable=True),
        sa.Column("linked_parent_user_id", sa.String(length=26), nullable=True),
        sa.Column("linked_kid_user_id", sa.String(length=26), nullable=True),
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
            ["linked_parent_user_id"],
            ["users.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["linked_kid_user_id"],
            ["users.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_parent_consent_records_email",
        "parent_consent_records",
        ["parent_email"],
    )
    op.create_index(
        "ix_parent_consent_records_policy_version",
        "parent_consent_records",
        ["policy_version"],
    )
    op.create_index(
        "ix_parent_consent_records_recorded_at",
        "parent_consent_records",
        ["recorded_at"],
    )
    op.create_index(
        "ix_parent_consent_records_linked_parent_user_id",
        "parent_consent_records",
        ["linked_parent_user_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_parent_consent_records_linked_parent_user_id",
        table_name="parent_consent_records",
    )
    op.drop_index(
        "ix_parent_consent_records_recorded_at",
        table_name="parent_consent_records",
    )
    op.drop_index(
        "ix_parent_consent_records_policy_version",
        table_name="parent_consent_records",
    )
    op.drop_index(
        "ix_parent_consent_records_email",
        table_name="parent_consent_records",
    )
    op.drop_table("parent_consent_records")
