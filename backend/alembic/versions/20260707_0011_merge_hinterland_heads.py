"""Merge Hinterland rebrand and mainline migration heads.

Revision ID: 20260707_0011
Revises: 20260703_0010, 20260707_0007
Create Date: 2026-07-07

The live Hinterland dev database received the nullable ``users.firebase_uid``
migration from the rebrand workstream while main continued through the
rarity/observation/Sanctuary migrations. This no-op merge revision lets
Alembic upgrade either branch path to one current head.
"""

from __future__ import annotations

revision = "20260707_0011"
down_revision = ("20260703_0010", "20260707_0007")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
