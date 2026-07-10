"""Published taxonomy packs and post-clean CV cache.

Revision ID: 20260709_0015
Revises: 20260709_0014
Create Date: 2026-07-09
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20260709_0015"
down_revision = "20260709_0014"
branch_labels = None
depends_on = None


def _timestamps() -> tuple[sa.Column[object], sa.Column[object]]:
    return (
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
    )


def upgrade() -> None:
    op.create_table(
        "taxonomy_packs",
        sa.Column("id", sa.String(length=26), primary_key=True),
        sa.Column("pack_id", sa.String(length=80), nullable=False),
        sa.Column("version", sa.String(length=64), nullable=False),
        sa.Column("scope", sa.String(length=80), nullable=False),
        sa.Column("checksum_sha256", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("taxon_count", sa.Integer(), nullable=False),
        sa.Column("bucket", sa.String(length=128), nullable=False),
        sa.Column("object_name", sa.String(length=512), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        *_timestamps(),
        sa.UniqueConstraint("pack_id", "version", name="uq_taxonomy_packs_id_version"),
    )
    op.create_index(
        "ix_taxonomy_packs_active_scope",
        "taxonomy_packs",
        ["active", "scope"],
    )
    op.create_table(
        "cv_suggestion_cache",
        sa.Column("photo_sha256", sa.String(length=64), primary_key=True),
        sa.Column("model_version", sa.String(length=64), primary_key=True),
        sa.Column(
            "suggestions",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        *_timestamps(),
    )


def downgrade() -> None:
    op.drop_table("cv_suggestion_cache")
    op.drop_index("ix_taxonomy_packs_active_scope", table_name="taxonomy_packs")
    op.drop_table("taxonomy_packs")
