# Photo Moderation

Every photo a kid uploads is screened before it's visible in the app or submitted to iNaturalist. The screening runs out of band on the photos bucket, not in the API service, so that the observation-submission hot path never blocks on the moderation provider. This doc describes the pipeline, the design decisions it depends on, and the edges that need special handling.

The provider is **Cloud Vision SafeSearch** per [ADR 0009](adr/0009-moderation-provider-cloud-vision-safesearch.md). This doc still reads "the moderation provider" where the choice doesn't matter (the pipeline shape is provider-agnostic by design), and points to ADR 0009 for the per-label thresholds, region, and the alternatives that were considered and rejected.

Related reading: `architecture.md` (how moderation fits into the observation flow), `data-model.md` (the `review_queue` table and membership counters), `runbook.md` (incident response for quarantined photos), `adr/0005-gcp-target-architecture.md` (Eventarc + Cloud Tasks + Cloud Run for async work), `adr/0009-moderation-provider-cloud-vision-safesearch.md` (provider choice + thresholds).

## The pipeline at a glance

```
kid uploads photo
         │
         ▼                    V4 signed PUT from /v1/photos/presign
   gs://dragonfly-photos-<env>-<project>/pending/<obs_id>.jpg
         │
         │  Eventarc google.cloud.storage.object.v1.finalized
         ▼
   ┌──────────────────────────┐
   │ moderation Cloud Run     │
   │  ┌──────────────────┐    │
   │  │ moderation       │    │ provider-specific call
   │  │ provider         │    │ (e.g. Cloud Vision SafeSearch)
   │  └──────────────────┘    │
   └──────┬───────────────────┘
          │
     ┌────┴──────┐
     │           │
  clean       flagged
     │           │
     ▼           ▼
 observations/ quarantine/
     │           │
     │           │  + insert review_queue row
     │           │  + update observations row: quarantined=true
     │           │
     ▼           ▼
  inat_submit  teacher review queue
  (Cloud Tasks)
```

The moderation Cloud Run service is the only component that writes to `observations/` or `quarantine/` — the API service only writes to `pending/` (via signed URL), never to either resolved prefix.

## Design decisions baked into this pipeline

**Moderation is synchronous with photo arrival, not with observation submission.** The kid uploads to `pending/`, then calls `POST /v1/observations`. Those two API calls are independent. The observation submission persists the `observations` row before moderation has finished. The kid sees their celebration immediately. This is the correct trade-off for the kid UX (they never wait on the moderation provider) at the cost of a small window where the observations row exists but its photo isn't yet approved. The mobile app renders an observation-without-photo placeholder during that window.

**iNat submit waits for moderation.** We do not want to push an unmoderated photo to iNaturalist. The Cloud Tasks task that `inat_submit` consumes is enqueued by the moderation Cloud Run service on the clean path, not by the API service at submission time. If moderation takes 2 seconds, iNat submit starts 2 seconds later. If moderation flags the photo, no Cloud Tasks task is ever enqueued and iNat never sees it.

**Quarantine moves, it doesn't delete.** Flagged photos are copied to `quarantine/`, not deleted from storage. A human (teacher) review path can recover a false positive. The storage lifecycle rule on the photos account (Azure Storage management policy, applied by `infra-azure/phase-3b-blob-lifecycle.sh`; the GCS-era equivalent lived in `infra-gcp/main.tf`) deletes `quarantine/` objects after 90 days; by that point the review has either closed or been auto-rejected (see `runbook.md`). NOTE: the policy is applied by an operator running the phase-3b script — verify it is active on the target account before relying on the sweep.

**Failed moderation does not default-allow.** If the moderation provider errors (throttle, service outage, transient 5xx), the photo stays in `pending/` and the worker raises so the queue can retry per its policy. The storage lifecycle rule (phase-3b management policy) clears abandoned `pending/` objects after 1 day; if moderation hasn't succeeded by then, the photo is gone and the observation's `photo_key` points nowhere — the mobile app treats this as a failed observation and surfaces it in the kid's "retry" list. This is intentional. Defaulting to "allow" on moderation failure means every moderation-provider outage becomes a content-safety incident.

## Moderation provider configuration

The moderation Cloud Run service calls Cloud Vision SafeSearch (`images.annotate` with feature `SAFE_SEARCH_DETECTION`) per [ADR 0009](adr/0009-moderation-provider-cloud-vision-safesearch.md). Provider-agnostic config:

- A confidence-or-likelihood threshold (provider-specific scale; tune after gathering false-positive data on a kid-photo test set).
- A label set that triggers flagging (e.g. `adult`, `violence`, `racy` for SafeSearch). Pinned to a config version so a provider update doesn't silently change behavior.

Flag rule: **flag if any configured label in the response meets the threshold.** Threshold and label set are loaded from Secret Manager (or runtime env) at cold start:

- `DRAGONFLY_MODERATION_THRESHOLD` (provider-specific scale)
- `DRAGONFLY_MODERATION_FLAG_LABELS` (JSON array)

Changing the rule is a Cloud Run revision env-var update, no full redeploy required. That's deliberate — if we hit a real-world incident we need to tighten fast.

## What the moderation Cloud Run service writes

On the clean path:

1. GCS object copy from `pending/<obs_id>.jpg` to `observations/<obs_id>.jpg`.
2. GCS delete on the `pending/` object.
3. `UPDATE observations SET photo_key = :new, moderation_status = 'clean' WHERE id = :obs_id`.
4. `cloudtasks:CreateTask` against the iNat submit queue with the observation ID.

On the flag path:

1. GCS object copy from `pending/<obs_id>.jpg` to `quarantine/<obs_id>.jpg`.
2. GCS delete on the `pending/` object.
3. `UPDATE observations SET photo_key = :quarantine_key, moderation_status = 'quarantined', moderation_labels = :labels WHERE id = :obs_id` (labels stored for the teacher review UI to explain *why*).
4. `INSERT INTO review_queue (id, group_id, photo_id, observation_id, status, ...)` with `status = 'pending'`. Indexed by `(group_id, status)` for fast teacher-review listing per group.
5. No iNat submit task.

On the error path (provider 5xx, network fault):

1. No GCS moves.
2. The Cloud Run service raises a non-2xx; Eventarc retries the trigger per its retry policy (configurable on the trigger resource).
3. After exhausting retries, Eventarc routes to a dead-letter Pub/Sub topic if configured; `runbook.md` covers the response.
4. The lifecycle rule on `pending/` (phase-3b management policy) eventually cleans the stuck photo after 1 day.

## Teacher review lifecycle

Quarantined photos are resolved by a teacher approving or rejecting the `review_queue` row from the mobile app (Week 11 in `roadmap.md`).

- **Approve.** Move photo from `quarantine/` to `observations/`, update the observations row (`moderation_status = 'approved_on_review'`, clear the quarantine flag), enqueue the delayed iNat submit (Cloud Tasks), mark the `review_queue` row as `approved` with reviewer id and timestamp.
- **Reject.** Delete the observations row and its `dex_entries` row (if any), decrement the user's membership counters, mark the `review_queue` row as `rejected`. The photo in `quarantine/` is left for the 90-day storage lifecycle policy (phase-3b) to sweep — we keep it briefly for audit and appeal.
- **Stale (no decision in 30 days).** The nightly sweep (`scripts/sweep_stale_reviews.py`) auto-rejects the review and runs the rejection path. See `runbook.md`.

Counters stay correct across all three paths because approve-on-review is an idempotent no-op on the counters (they were never bumped at submission, because on the flag path we don't bump) — wait, that's wrong. Let me be precise: `observation_count` *is* bumped at submission (by the submission transaction, before moderation has run). A flagged observation's `observation_count` stays bumped until the teacher reviews. On approve, nothing changes. On reject, the counter decrements. This is the correct semantic: the kid's observation count is what they've *submitted*, not what's been approved; the Dex (which is only written by `DexHandler` after moderation, per ADR 0004) is what's been *earned*.

Actually — `DexHandler` runs at submission time, not after moderation. So a first-find on a photo that later gets quarantined will have its `dex_entries` row and `dex_count` bumped, and on reject those need to be un-done. This is handled by the rejection path: `DELETE` the `dex_entries` row, `UPDATE memberships SET dex_count = dex_count - 1 WHERE user_id = ...`.

## What this doc doesn't cover

- **Text moderation** — if and when we add observation notes (kids can type a short caption). Plan is to OCR the photo (provider's text-detection API) for any text appearing in the image, then send the extracted text plus the kid's caption through a text-moderation model, but that's a Phase 3 discussion at earliest.
- **Appeals.** A rejected kid currently has no in-app path to contest. If appeals become a real need, the `REVIEW#` row gets a second status field and the workflow grows. Not for Phase 1.
- **Moderation metrics dashboard.** Counts by label, false-positive rate against teacher-overridden approvals, per-kid flag rate. These are observability improvements for post-beta.
