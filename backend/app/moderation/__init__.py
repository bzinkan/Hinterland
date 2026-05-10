"""Photo moderation worker.

Per ADR 0009 the gate is Cloud Vision SafeSearch in production. The
worker runs out of band on the GCS finalize event for the `pending/`
prefix (Eventarc trigger -> internal endpoint), reads the photo,
classifies it, and either:

- copies to `observations/<id>.jpg` + flips Photo.status to "clean"
  + (Phase 8 follow-up) enqueues an iNat-submit Cloud Tasks job
- copies to `quarantine/<id>.jpg` + flips Photo.status to "quarantine"
  + inserts a `review_queue` row for the group's teacher

Failure modes never default-allow: if SafeSearch is unreachable, the
worker raises so Eventarc retries; the kid's observation is unaffected
(it was created before any of this ran -- the trade-off documented in
`docs/moderation.md`).
"""
