"""allow non-Firebase users

Revision ID: 20260707_0007
Revises: 20260604_0006
Create Date: 2026-07-07

The Entra/kid-token auth model keeps ``users.firebase_uid`` only as a legacy
rollback identifier. Adult Entra users and backend-minted kid users do not
have Firebase IDs, so the database must match the nullable SQLAlchemy model.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260707_0007"
down_revision = "20260604_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "users",
        "firebase_uid",
        existing_type=sa.String(length=128),
        nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "users",
        "firebase_uid",
        existing_type=sa.String(length=128),
        nullable=False,
    )
