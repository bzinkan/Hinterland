"""phase 6 entra identity columns + kid handoff jti table

Revision ID: 20260602_0003
Revises: 20260510_0002
Create Date: 2026-06-02

Phase 6a introduces Microsoft Entra ID as the adult identity provider and a
new Hinterland RS256 token path for kid sessions. This migration:

(a) adds ``users.entra_oid`` (Entra Object ID) -- nullable + unique +
    indexed for the auth dependency's hot-path lookup. Adults are resolved
    by ``entra_oid`` from the verified Entra v2 access-token ``oid`` claim.

(b) intentionally leaves ``users.disabled_at`` UNTOUCHED. It was added by
    the foundation migration ``20260506_0001``; re-adding it here would
    raise ``DuplicateColumn``.

(c) intentionally leaves ``users.firebase_uid`` (and its unique constraint)
    UNTOUCHED. They are retained through Phase 9 so Phase 10 can drop them
    in one shot after Firebase rollback is no longer required.

(d) creates ``kid_handoff_jti`` to enforce single-use for the short-lived
    handoff JWT minted by ``/v1/groups/{id}/kids``. The ``POST
    /v1/auth/kid-exchange`` handler atomically claims a row -- a duplicate
    PK violation is the single-use gate. The ``ix_kid_handoff_jti_expires_at``
    index supports the background sweeper that purges rows past
    ``expires_at + 7 days`` (sweeper lands in Phase 11 observability work).
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260602_0003"
down_revision = "20260510_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # (a) users.entra_oid -- Entra Object ID for adult identity lookup
    op.add_column(
        "users",
        sa.Column("entra_oid", sa.String(length=64), nullable=True),
    )
    op.create_unique_constraint("uq_users_entra_oid", "users", ["entra_oid"])
    op.create_index("ix_users_entra_oid", "users", ["entra_oid"])

    # (d) kid_handoff_jti -- single-use ledger for kid handoff tokens
    op.create_table(
        "kid_handoff_jti",
        sa.Column("jti", sa.Text(), nullable=False),
        sa.Column("kid_user_id", sa.String(length=26), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
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
            ["kid_user_id"],
            ["users.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("jti"),
    )
    op.create_index(
        "ix_kid_handoff_jti_expires_at",
        "kid_handoff_jti",
        ["expires_at"],
    )


def downgrade() -> None:
    # Strict reverse order: table first, then column/constraint/index.
    op.drop_index("ix_kid_handoff_jti_expires_at", table_name="kid_handoff_jti")
    op.drop_table("kid_handoff_jti")
    op.drop_index("ix_users_entra_oid", table_name="users")
    op.drop_constraint("uq_users_entra_oid", "users", type_="unique")
    op.drop_column("users", "entra_oid")
    # NB: does NOT drop users.disabled_at -- the foundation migration owns it.
