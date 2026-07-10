# Android Internal pilot stop plan

How to stop the W1 Google Play Internal testing pilot fast, preserve
the evidence, and tell the testers honestly. This is the "something is
wrong, pull the plug" playbook — intentionally short and biased
toward stopping before diagnosing.

Sister docs:
- [`docs/one-week-kid-pilot-checklist.md`](one-week-kid-pilot-checklist.md)
  for the strict gates (this doc fires when one of those gates is
  failing in flight, or when one of the hard-stop triggers below
  surfaces during a real session).
- [`docs/android-internal-pilot-test-script.md`](android-internal-pilot-test-script.md)
  for the tester walkthrough that a clean rerun must pass before
  resuming.
- [`docs/runbook.md`](runbook.md)
  for canonical operational commands. The runbook is GCP/Cloud-Run-
  shaped; the W1 pilot runs against the Azure Container App per the
  in-flight Phase 11 migration, so the rollback patterns here cite
  the runbook's logic but use `az containerapp` equivalents.

Philosophy: **stop fast, preserve evidence, communicate honestly.**
Speed of stopping matters more than knowing the root cause. Do not
delete rows or clear logs to "tidy up" before snapshotting — the
`parent_consent_records` row and the kid `users.id` row are the COPPA
audit trail and they stay put until the snapshot is taken.

## Hard-stop triggers

These are the conditions that trigger this playbook. Any ONE of them
is a hard stop — do not try to fix in flight, do not "just finish the
session," do not "see if it reproduces." They are the same six
triggers listed in
[`docs/one-week-kid-pilot-checklist.md`](one-week-kid-pilot-checklist.md)
under "Hard stop conditions" — by design, the wording is identical so
the two docs cannot drift:

- auth failure exposing wrong user data
- child can reach public chat or social features
- photo appears publicly without a moderation decision
- crash during submit that loses kid data
- incorrect consent state
- location / privacy surprise
- unauthorized photo URL or any pre-clean/external photo egress
- retry/process death duplicates or loses kid data
- raw coordinate or account-switch privacy surprise

The expanded definitions below add the operator-facing detail; the
six bullets above are the canonical list.

### Auth failure exposing wrong user data
Parent A signs in and sees parent B's group, kids, or observations.
Any cross-tenant leak.

### Child can reach public chat or social features
Per AGENTS.md this is forbidden in Phase 1. If a kid can free-text to
another user, a public feed, or any outbound social surface, stop.

### Photo appears publicly without a moderation decision
W1 NoOp is `pilot_private`, not approval. It grants no signed URL and creates no
iNaturalist work. No pending, pilot-private, quarantine, failed, or rejected
photo may leave Azure.

### Unauthorized photo URL or pre-clean/external egress
Any owner, peer, or adult receives a signed URL outside the documented matrix,
or a non-clean photo reaches a third party.

### Retry or process death duplicates/loses kid data
The durable queue/JPEG vanishes before canonical reconciliation, or repeated
presign/create produces multiple observations, counter bumps, or reward sets.

### Raw coordinate or account-switch privacy surprise
Any raw latitude/longitude appears in PostgreSQL, logs, URLs, or analytics, or
one child's photo/cache/draft/queue state appears for another child.

### Crash during submit that loses kid data
The queue/JPEG or authoritative Observation vanishes. Stuck retryable work is
acceptable; vanished or duplicated work is not.

### Incorrect consent state
Consent missing for a kid that was provisioned; consent linked to the
wrong parent; or a consent receipt `id` (the ULID returned from
`POST /v1/auth/consent`) that does not match the row in
`parent_consent_records`.

### Location / privacy surprise
Any fine/raw location captured, persisted, logged, displayed, or shared beyond
the optional `geohash4`/no-location contract in
[`docs/risks/0007-google-play-families-location-policy.md`](risks/0007-google-play-families-location-policy.md).

## Stop sequence

In priority order. Do the cheap, immediate-effect actions first.

- [ ] **Remove the tester group from Play Console Internal testing.**
  Fastest way to prevent new sessions from starting — removed testers
  cannot install or update from the opt-in link. Note: removed
  testers keep their already-installed build (Play Console cannot
  remote-wipe it); only NEW installs and updates are blocked.
- [ ] **If the bug is build-side** (crash, wrong screen, broken UI):
  pause the Internal testing release in Play Console. Existing
  installs stay on-device but no new install or update can fetch the
  AAB.
- [ ] **If the bug is API-side**: disable the relevant risky surface
  at the Container App env-var layer. The W1 pilot runs on Azure
  Container Apps; use `az containerapp revision` / `az containerapp
  update --set-env-vars` patterns.
  - **iNat submission**: confirm `HINTERLAND_INAT_OAUTH_TOKEN` is
    unset (it should already be unset for W1 per
    [`docs/risks/0002-async-workers-production-unwired.md`](risks/0002-async-workers-production-unwired.md);
    if it somehow got set, unset it). This is the canonical gate
    that keeps observations off the public iNaturalist project.
  - **Moderation**: in W1, moderation runs the noop path because
    `HINTERLAND_MODERATION_PROVIDER=cloud_vision_safesearch` is
    intentionally NOT set per risk 0002 (Cloud Vision API not yet
    enabled; cost meter starts on flip). No moderation kill-switch
    is needed during W1. If you suspect moderation has been flipped
    on out-of-band, confirm via `az containerapp show` + grep for
    `HINTERLAND_MODERATION_PROVIDER` and unset it.
  - **Internal worker routes**: `/internal/*` can be hard-disabled
    by clearing either `HINTERLAND_INTERNAL_OIDC_AUDIENCE` or
    `HINTERLAND_INTERNAL_OIDC_ALLOWED_SERVICE_ACCOUNTS`. The backend
    then fail-closes every `/internal/*` request with
    `503 internal_oidc_misconfigured`. This is the documented kill
    lever for moderation / iNat worker routes.
  - **If the bug is in the API itself** (not config): do a
    revision-pinned rollback. The runbook documents the Cloud Run
    pattern (`gcloud run services update-traffic hinterland-api
    --region us-central1 --to-revisions REVISION_NAME=100`); the
    Azure-side equivalent is `az containerapp revision set-mode
    single --revision <previous>` (or the multiple-revision
    weighted equivalent). After rollback, re-run smoke probes
    (`/health`, `/ready`, `/v1/meta`) per
    [`docs/runbook.md`](runbook.md) and check logs for
    `api.startup`, `api.shutdown`, and `api.unhandled_exception`.
- [ ] **Do NOT clear logs.** Do NOT delete the affected
  `parent_consent_records` row. Do NOT delete the kid's `users.id`
  row or the parent's `users.id` row. Evidence > speed. The consent
  ledger is what we point a COPPA auditor at; deleting "to clean up"
  destroys the audit trail.

## Evidence preservation

Do this BEFORE debugging, not after.

- [ ] Capture a screen recording from the kid's device if possible
  (parent's phone is fine if the kid was using the parent's device).
  Even a 10-second clip of the failing screen is enough.
- [ ] For every family in-flight when the bug fired, note the
  `parent_consent_records.id` (the 26-char ULID) in the session
  journal. This is the per-family receipt; it is the join key for
  every other piece of evidence.
- [ ] Snapshot relevant database state — at minimum the
  `parent_consent_records` row(s), the affected `users` row(s), and
  any `observations` row(s) involved. The runbook documents psql
  inspection patterns ([`docs/runbook.md`](runbook.md)); for W1 the
  same `select id, parent_email, policy_version, recorded_at from
  parent_consent_records order by recorded_at desc limit 3;` form
  used in Gate 1 of the checklist is the starting point. Prefer
  read-only `SELECT`; do NOT run anything destructive.
- [ ] Pull Container App logs for the relevant time window. At
  minimum capture `auth.consent.recorded`, `api.unhandled_exception`,
  and any `cleanup_smoke.*` entries that overlap the window. Save
  the output to a local file — do not rely on the log retention to
  still hold it tomorrow.

## Communication to testers

Tone: brief, no-blame, no detail. The tester allowlist is OUT of the
repo (private spreadsheet, per the pilot checklist).

- [ ] Send a short private email to the tester allowlist. One email
  per family is fine; BCC is also fine. Direct email only — no group
  chat, no public post, no Slack channel that has any non-tester in
  it.
- [ ] Say: testing is paused; you will be re-invited when the issue
  is fixed; no action needed from you; thank you for the time you
  already gave us.
- [ ] Do NOT include: PII of the affected child (no name, no photo,
  no location), root-cause speculation ("we think it's an auth provider
  bug..."), or internal links (no Slack, no Jira, no GitHub issue
  URLs, no Cloud Logging / Application Insights deep links).

## Resume criteria

All four must hold before re-inviting any tester. No partial resume.

- [ ] Reproducible repro on a dev or throwaway account. "I couldn't
  reproduce it" is not a fix; it means the stop stays in effect
  until either the repro is found or enough evidence accumulates to
  rule it out.
- [ ] Fix landed on `main` and CI is green. No long-lived branches;
  the fix is on main before the pilot resumes.
- [ ] One full clean run of
  [`docs/android-internal-pilot-test-script.md`](android-internal-pilot-test-script.md)
  on Brian's own throwaway account, end to end, no regressions. The
  consent-ledger gates (consent row lands, ULID matches, parent
  signup back-stamps `linked_parent_user_id`) must all pass.
- [ ] Tester allowlist re-confirmed (still the same 1–3 families,
  still in scope under
  [`docs/risks/0007-google-play-families-location-policy.md`](risks/0007-google-play-families-location-policy.md)'s
  chosen option) before the next invitation email goes out.

## What this doc is NOT

- Not the gate checklist — that's
  [`docs/one-week-kid-pilot-checklist.md`](one-week-kid-pilot-checklist.md).
- Not the test script — that's
  [`docs/android-internal-pilot-test-script.md`](android-internal-pilot-test-script.md).
- Not the policy decision authority for location — that's
  [`docs/risks/0007-google-play-families-location-policy.md`](risks/0007-google-play-families-location-policy.md).
  This stop plan only enforces whatever option risk 0007 already
  committed to; it does not pick or change that option.
