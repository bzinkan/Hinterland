"""rarity_cache.iconic_taxon column

Revision ID: 20260702_0007
Revises: 20260604_0006
Create Date: 2026-07-02

Adds `iconic_taxon` to `rarity_cache` so Phase-4 expedition ranking can
ask "which iconic groups (Aves, Insecta, ...) are actually observed in
region X" without a per-taxon lookup. The nightly rarity refresh already
receives `taxon.iconic_taxon_name` from iNat's species_counts payload;
the parser previously dropped it.

Backfill: deliberately none. NULL = "unknown until the next nightly
refresh" -- the refresh upserts every cached (region, taxon) row on each
run, so the column self-populates within one nightly cycle. iNat also
returns no iconic taxon for some taxa, so NULL stays a legitimate
steady-state value and consumers must tolerate it either way.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260702_0007"
down_revision = "20260604_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "rarity_cache",
        sa.Column("iconic_taxon", sa.String(length=32), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("rarity_cache", "iconic_taxon")
