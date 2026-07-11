"""Bind parent-consent receipts to a browser-held nonce.

Revision ID: 20260711_0018
Revises: 20260710_0017
Create Date: 2026-07-11

Historical consent rows predate browser-bound proof, so the additive column is
nullable. The current API requires a fresh 256-bit nonce and always writes its
SHA-256 digest. Parent signup fails closed for a legacy NULL digest.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260711_0018"
down_revision = "20260710_0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "parent_consent_records",
        sa.Column("browser_nonce_sha256", sa.String(length=64), nullable=True),
    )
    op.create_unique_constraint(
        "uq_parent_consent_browser_nonce_sha256",
        "parent_consent_records",
        ["browser_nonce_sha256"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_parent_consent_browser_nonce_sha256",
        "parent_consent_records",
        type_="unique",
    )
    op.drop_column("parent_consent_records", "browser_nonce_sha256")
