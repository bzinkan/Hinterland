"""Nightly rarity refresh.

Per `docs/rarity-pipeline.md` this runs as a Cloud Run Job at 03:00 UTC
nightly. It walks every geohash-4 cell where Hinterland has observed
something recently, asks iNat what species are present in that cell's
bounding box, buckets each species into a tier by share of cell-total,
and upserts to `rarity_cache`.

Phase 8 implementation deliberately omits:
- Cursor self-continuation (job_state row writes mid-walk)
- geohash-3 fallback for low-data cells
- Per-region observability log lines

Those are docs/rarity-pipeline.md "Trigger and shape" + "low_data and
the geohash-3 fallback" sections; both are tracked as Phase 8 follow-ups.
"""
