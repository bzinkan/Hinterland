"""Write-once first-taxon marker on observations.

Revision ID: 20260703_0010

The taxon-time re-dispatch previously gated only on the None -> taxon
transition plus a dex-mint probe, so a raw-API clear-and-repick
(PATCH taxon_id=null, then a new taxon) could run the full reward
dispatch a second time -- re-emitting rarity celebrations and advancing
a different expedition with a different taxon on the same photo.

`taxon_first_assigned_at` records when a taxon FIRST landed on the
observation and is never cleared; the re-dispatch requires it to be
NULL. Backfill: any existing identified observation gets the marker
(dispatched_at when present, else created_at) so the loophole closes
for pre-migration rows too. Rows whose taxon was assigned then cleared
before this migration are unknowable from schema -- the dex-mint probe
in the PATCH route remains as belt-and-braces for that residual.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260703_0010"
down_revision = "20260703_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "observations",
        sa.Column("taxon_first_assigned_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(
        "UPDATE observations "
        "SET taxon_first_assigned_at = COALESCE(dispatched_at, created_at) "
        "WHERE taxon_id IS NOT NULL"
    )


def downgrade() -> None:
    op.drop_column("observations", "taxon_first_assigned_at")
