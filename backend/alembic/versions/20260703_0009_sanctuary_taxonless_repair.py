"""Repair pre-fix taxonless Sanctuary contribution rows.

Revision ID: 20260703_0009

Before the WorldHandler taxonless skip, every live-flow observation
(created without a taxon) claimed its `sanctuary_observation_contributions`
row at create time with `taxon_id` NULL, routed to the throwaway
`elsewhere` zone. Those rows are poison: the taxon-time re-dispatch hits
the PK conflict and short-circuits, so identifying such an observation
can never earn its real zone contribution.

Repair, in order:

1. Delete the taxonless contribution rows (they are exactly the pre-fix
   claims -- post-fix, a taxonless observation never writes one).
2. Recompute per-(user, zone) observation counts and depth tiers from
   the remaining contributions, so `elsewhere` stops carrying phantom
   credit. Unlocked elements/events are left untouched: they were
   granted celebrations and taking them back would be a kid-visible
   regression.
3. Reset `dispatched_at` for identified observations that now lack a
   contribution row -- the dispatcher replay job picks them up
   (`dispatched_at IS NULL`) and re-runs the full handler list, which
   is per-observation idempotent everywhere except the now-possible
   Sanctuary contribution. This recovers observations that were
   identified while the bug was live (their re-dispatch was blocked).

Irreversible data repair: downgrade is a no-op.
"""

from __future__ import annotations

from alembic import op

revision = "20260703_0009"
down_revision = "20260702_0008"
branch_labels = None
depends_on = None

# depth_tier stores the highest threshold VALUE reached (1/3/5/10/20/50),
# not an ordinal -- see service.py _build_zone_transition (after_tier =
# max(crossed)) and the mobile pip rendering (tier <= depth_tier).
_RECOMPUTE_ZONE_STATE = """
UPDATE sanctuary_zone_state z
SET observation_count = sub.cnt,
    depth_tier = CASE
        WHEN sub.cnt >= 50 THEN 50
        WHEN sub.cnt >= 20 THEN 20
        WHEN sub.cnt >= 10 THEN 10
        WHEN sub.cnt >= 5 THEN 5
        WHEN sub.cnt >= 3 THEN 3
        WHEN sub.cnt >= 1 THEN 1
        ELSE 0
    END
FROM (
    SELECT zs.user_id,
           zs.zone_id,
           (
               SELECT COUNT(*)
               FROM sanctuary_observation_contributions c
               WHERE c.user_id = zs.user_id AND c.zone_id = zs.zone_id
           ) AS cnt
    FROM sanctuary_zone_state zs
) sub
WHERE z.user_id = sub.user_id AND z.zone_id = sub.zone_id
"""

_REQUEUE_BLOCKED_OBSERVATIONS = """
UPDATE observations
SET dispatched_at = NULL
WHERE taxon_id IS NOT NULL
  AND NOT EXISTS (
      SELECT 1
      FROM sanctuary_observation_contributions c
      WHERE c.observation_id = observations.id
  )
"""


def upgrade() -> None:
    op.execute("DELETE FROM sanctuary_observation_contributions WHERE taxon_id IS NULL")
    op.execute(_RECOMPUTE_ZONE_STATE)
    op.execute(_REQUEUE_BLOCKED_OBSERVATIONS)


def downgrade() -> None:
    # Data repair; nothing sensible to restore.
    pass
