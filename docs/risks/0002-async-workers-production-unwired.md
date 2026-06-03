# Risk 0002: Phase 8 async workers exist but aren't production-wired

- **Status:** Open
- **Date filed:** 2026-05-10
- **Source:** Phase 8 exit criteria ("submitted observation appears in iNaturalist within target window" + "flagged test photo is quarantined and reviewable")
- **Owner:** Brian (requires GCP infra changes + iNat OAuth token from risk 0001)

## What we have

Phase 8 shipped the worker code for moderation, iNat submit, and rarity refresh. All three have unit-test coverage of the third-party outage paths (Cloud Vision 5xx, iNat 5xx, transport errors) and of the success paths against `respx`-mocked APIs.

**Concrete deliverables:**

- **Moderation** (PR #44): `app.moderation.provider.CloudVisionSafeSearchModerator`, `app.moderation.processor.process_pending_photo`, `POST /internal/moderation/process`. NoOp default for dev / CI; flips to SafeSearch with `DRAGONFLY_MODERATION_PROVIDER=cloud_vision_safesearch`.
- **Review queue** (PR #45): `GET /v1/review-queue` + `POST .../approve` + `POST .../reject`. Adult-role gated, group-scoped, decrement counter on reject.
- **iNat submit** (PR #46): `app.inat.submit.submit_observation_to_inat`, `POST /internal/inat/submit`. Uses Dragonfly observation id as the iNat uuid for Cloud Tasks idempotency.
- **Rarity refresh** (PR #47): `app.rarity.refresh.run_refresh`, `admin/rarity_refresh.py` Cloud Run Job entry point. Same admin-task pattern as `cleanup_smoke_users`.

## What's NOT wired in production

These pieces are missing and require human-driven GCP setup:

### 1. Cloud Vision SafeSearch enablement

- **What's needed**: Enable the Cloud Vision API on `dragonflyapp-495423` (`gcloud services enable vision.googleapis.com`); verify the runtime SA `dragonfly-api-dev@…` has `roles/serviceusage.serviceUsageConsumer` (it does, per PR #24); set `DRAGONFLY_MODERATION_PROVIDER=cloud_vision_safesearch` on the Cloud Run service.
- **Cost**: ~$1.50/1000 SafeSearch requests per ADR 0009. At 1000 obs/day = ~$45/month.
- **Why deferred**: Cost meter starts ticking the moment we flip it. We have no observations yet.

### 2. Eventarc → moderation worker wiring

- **What's needed**: A `google_eventarc_trigger` resource that fires on GCS `pending/` finalize events at the photos bucket, with destination = the existing Cloud Run service's `POST /internal/moderation/process`. Plus OIDC token verification on the `/internal/` route (currently unauthenticated).
- **Why deferred**: Real testing needs the Vision API enabled AND a kid actually uploading a photo. We can wire the trigger as soon as #1 is done.
- **Sketch**:
  ```hcl
  resource "google_eventarc_trigger" "moderation_pending" {
    name = "dragonfly-moderation-pending"
    location = var.region
    matching_criteria {
      attribute = "type"
      value = "google.cloud.storage.object.v1.finalized"
    }
    matching_criteria {
      attribute = "bucket"
      value = google_storage_bucket.photos.name
    }
    destination {
      cloud_run_service {
        service = google_cloud_run_v2_service.api.name
        path = "/internal/moderation/process"
        region = var.region
      }
    }
    service_account = google_service_account.api.email
  }
  ```

### 3. Cloud Tasks → iNat submit wiring

- **What's needed**: `google_cloud_tasks_queue` for `inat_submit` with retry config + DLQ topic. The moderation worker on the clean path should `POST` a task to that queue with the observation id; the queue invokes `POST /internal/inat/submit` via OIDC.
- **Why deferred**: Needs the iNat OAuth token (risk 0001) before any task body would succeed. A token-less rollout would just DLQ everything.

### 4. Cloud Scheduler → rarity refresh wiring

- **What's needed**: A nightly `0 3 * * *` Cloud Scheduler job that triggers `dragonfly-rarity-refresh` (a Cloud Run Job using the same image as `dragonfly-api`, command `python -m admin.rarity_refresh`). Same shape as the `dragonfly-cleanup-smoke-nightly` cron we already have in Terraform (PR #26).
- **Why deferred**: Same iNat-token blocker as above. A token-less run would skip every region.

### 5. OIDC verification on `/internal/*` routes -- **IMPLEMENTED**

- **Code**: `app/core/internal_auth.py` (PR `feat/internal-oidc-auth`).
  Router-level dependency `require_internal_oidc` is applied to both
  `app/api/routes/internal_moderation.py` and
  `app/api/routes/internal_inat.py`. The verifier delegates to
  `google.oauth2.id_token.verify_oauth2_token` (lazy-imported so the
  local-dev path doesn't need google-auth on hand), pins audience to
  `settings.internal_oidc_audience`, and gates by allowlist
  `settings.internal_oidc_allowed_service_accounts`.
- **Env-driven config** (`DRAGONFLY_` prefix):
  - `DRAGONFLY_INTERNAL_OIDC_REQUIRED` (`bool | None`, default `None`).
    `None` resolves to "required whenever env != local"; explicit
    `true` / `false` overrides in either direction.
  - `DRAGONFLY_INTERNAL_OIDC_AUDIENCE` -- the Cloud Run service URI or
    operator-chosen audience string. Required when OIDC is on.
  - `DRAGONFLY_INTERNAL_OIDC_ALLOWED_SERVICE_ACCOUNTS` -- JSON array of
    invoker emails (pydantic parses comma- or JSON-list forms). Required
    when OIDC is on.
- **Fail-closed**: when OIDC is required but audience or allowlist is
  empty, every `/internal/*` request returns **503 internal_oidc_misconfigured**
  rather than silently letting traffic through.
- **Error contract**: 401 for missing / malformed / invalid token,
  401 for token-missing-email, 403 for non-allowlisted email, 503 for
  config drift.
- **Tests**: `backend/tests/test_internal_auth.py` covers all eight
  cases above plus integration checks that the real
  `/internal/moderation/process` and `/internal/inat/submit` routes
  reject missing auth BEFORE any DB session work happens.
- **What still needs operator action**:
  - Cloud Run env vars set in Terraform (sketch below) once the
    invoker SA exists.
  - Eventarc / Cloud Tasks / Cloud Scheduler each configured to attach
    the Google-signed OIDC token with the matching audience. See the
    runbook for the per-service config patterns.

  Sketch (Terraform; lands in a follow-up PR if the operator wants
  Terraform-side rather than env-var-side):
  ```hcl
  env {
    name  = "DRAGONFLY_INTERNAL_OIDC_REQUIRED"
    value = "true"
  }
  env {
    name  = "DRAGONFLY_INTERNAL_OIDC_AUDIENCE"
    value = google_cloud_run_v2_service.api.uri
  }
  env {
    name  = "DRAGONFLY_INTERNAL_OIDC_ALLOWED_SERVICE_ACCOUNTS"
    # JSON list; pydantic-settings parses it back into list[str].
    value = jsonencode([
      google_service_account.api.email,
      # Add separate SAs here when Eventarc / Cloud Tasks / Scheduler
      # use distinct invoker identities.
    ])
  }
  ```

### 6. Schema gap: `observations.moderation_status` / `moderation_labels`

`docs/moderation.md` references columns that don't exist on the model. The processor stores moderation labels in `review_queue.reason` (JSON-encoded text) as a workaround. A future Alembic migration adds the proper columns and the processor's photo-update step writes them.

## Production unblock checklist

Order matters — each step depends on the previous.

- [ ] Close risk 0001 (iNat OAuth token, manual signup)
- [ ] Enable Cloud Vision API on `dragonflyapp-495423`
- [ ] Set `DRAGONFLY_MODERATION_PROVIDER=cloud_vision_safesearch` on Cloud Run
- [ ] Wire OIDC verification middleware on `/internal/*`
- [ ] Add Terraform: Eventarc trigger pointing at `/internal/moderation/process`
- [ ] Add Terraform: Cloud Tasks queue + DLQ for `inat_submit`; wire moderation worker's clean-path callback
- [ ] Add Terraform: Cloud Scheduler `0 3 * * *` cron triggering the `dragonfly-rarity-refresh` Cloud Run Job (job spec same shape as `dragonfly-cleanup-smoke`)
- [ ] Add Alembic migration for `observations.moderation_status` + `observations.moderation_labels` columns; backfill from existing `photos.status`
- [ ] End-to-end verification: take a real photo via mobile, confirm it goes pending → observations within 30s, confirm an iNat observation appears within the target window
- [ ] End-to-end verification of the flagged path: upload a known-flagged test image, confirm it lands in quarantine + a review_queue row appears + a teacher account can approve/reject

## Mitigation in the meantime

Without any of the above wired, **the kid experience is unaffected**:

- Observation submission (Phase 6) returns success the moment the kid submits — moderation never blocked the hot path
- The kid sees their observation in My Observations regardless of moderation state (it shows `photo_status: pending` until something runs)
- iNat submission isn't visible to the kid; not happening just means the science aggregation doesn't accumulate yet

The moderation worker code, review queue endpoints, iNat submitter, and rarity refresh are all built and tested. They'll start doing real work the moment the GCP wiring lands. Until then, every photo a kid uploads stays in `pending/` and is cleaned by the existing 24h GCS lifecycle rule.

## Related risks

- [Risk 0001](0001-inat-cv-correctness-target-unverified.md) — iNat OAuth token blocker is shared with this risk for items #3 (iNat submit) and #4 (rarity refresh).
