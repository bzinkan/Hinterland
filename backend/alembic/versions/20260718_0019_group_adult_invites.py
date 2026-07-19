"""Privacy-safe adult invitations and active group memberships.

Revision ID: 20260718_0019
Revises: 20260711_0018
Create Date: 2026-07-18

The migration is additive and may run before the API. It deliberately aborts
if legacy data contains a child in more than one group; silently choosing a
family would be a privacy-affecting product decision.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260718_0019"
down_revision = "20260711_0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "groups",
        sa.Column(
            "shared_groups_enabled_at", sa.DateTime(timezone=True), nullable=True
        ),
    )
    op.add_column(
        "memberships",
        sa.Column("status", sa.String(length=16), nullable=True, server_default="active"),
    )
    op.add_column(
        "memberships",
        sa.Column("left_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "memberships",
        sa.Column(
            "management_ref",
            sa.String(length=32),
            nullable=True,
            server_default=sa.text("md5(random()::text || clock_timestamp()::text)"),
        ),
    )
    op.add_column(
        "memberships",
        sa.Column(
            "session_version", sa.Integer(), nullable=True, server_default="1"
        ),
    )
    op.execute(sa.text("UPDATE memberships SET status = 'active' WHERE status IS NULL"))
    op.execute(
        sa.text(
            "UPDATE memberships "
            "SET management_ref = md5(id || '-group-management') "
            "WHERE management_ref IS NULL"
        )
    )
    op.create_unique_constraint(
        "uq_memberships_management_ref", "memberships", ["management_ref"]
    )
    op.alter_column("memberships", "management_ref", nullable=False)
    op.execute(
        sa.text(
            "UPDATE memberships SET session_version = 1 "
            "WHERE session_version IS NULL"
        )
    )
    op.alter_column("memberships", "session_version", nullable=False)
    op.create_check_constraint(
        "ck_memberships_session_version",
        "memberships",
        "session_version >= 1",
    )

    op.execute(
        sa.text(
            """
            DO $$
            BEGIN
              IF EXISTS (
                SELECT m.user_id
                  FROM memberships m
                 WHERE m.role = 'kid'
                 GROUP BY m.user_id
                HAVING count(*) > 1
              ) THEN
                RAISE EXCEPTION
                  'A kid belongs to multiple groups; reconcile before group migration';
              END IF;
            END $$
            """
        )
    )

    op.alter_column("memberships", "status", nullable=False)
    op.create_check_constraint(
        "ck_memberships_status",
        "memberships",
        "status in ('active', 'left')",
    )
    op.create_check_constraint(
        "ck_memberships_status_left_at",
        "memberships",
        "(status = 'active' AND left_at IS NULL) OR "
        "(status = 'left' AND left_at IS NOT NULL)",
    )
    op.create_index(
        "uq_memberships_active_kid_user",
        "memberships",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("role = 'kid' AND status = 'active'"),
    )

    op.create_table(
        "group_adult_invites",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("group_id", sa.String(length=26), nullable=False),
        sa.Column("created_by_user_id", sa.String(length=26), nullable=False),
        sa.Column("token_sha256", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("redeemed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("redeemed_by_user_id", sa.String(length=26), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by_user_id", sa.String(length=26), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "NOT (redeemed_at IS NOT NULL AND revoked_at IS NOT NULL)",
            name="ck_group_adult_invites_terminal_state",
        ),
        sa.CheckConstraint(
            "(redeemed_at IS NULL) = (redeemed_by_user_id IS NULL)",
            name="ck_group_adult_invites_redeemed_pair",
        ),
        sa.CheckConstraint(
            "(revoked_at IS NULL) = (revoked_by_user_id IS NULL)",
            name="ck_group_adult_invites_revoked_pair",
        ),
        sa.ForeignKeyConstraint(
            ["group_id"], ["groups.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["redeemed_by_user_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["revoked_by_user_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_sha256", name="uq_group_adult_invites_token_sha256"),
    )
    op.create_index(
        "ix_group_adult_invites_group_expires",
        "group_adult_invites",
        ["group_id", "expires_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_group_adult_invites_group_expires", table_name="group_adult_invites"
    )
    op.drop_table("group_adult_invites")
    op.drop_index("uq_memberships_active_kid_user", table_name="memberships")
    op.drop_constraint(
        "uq_memberships_management_ref", "memberships", type_="unique"
    )
    op.drop_constraint(
        "ck_memberships_session_version", "memberships", type_="check"
    )
    op.drop_constraint("ck_memberships_status_left_at", "memberships", type_="check")
    op.drop_constraint("ck_memberships_status", "memberships", type_="check")
    op.drop_column("memberships", "left_at")
    op.drop_column("memberships", "management_ref")
    op.drop_column("memberships", "session_version")
    op.drop_column("memberships", "status")
    op.drop_column("groups", "shared_groups_enabled_at")
