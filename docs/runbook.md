# Hinterland Azure Runbook

ADR 0010 makes Azure the active runtime. ADR 0014 removes the old GCP/Firebase
rollback path. ADR 0015 defines Observation finalization, outbox-only
moderation, durable rewards, and rebuild recovery.

The release authority for Observation W1 is
[`observation-w1-promotion.md`](observation-w1-promotion.md). It defines
**W1-ready**, **W1 fully evidenced**, and **closed-beta promoted** as distinct
states. A green ordinary development deployment is not a W1 promotion.

## Environment Boundary

Active dev resources are in the Gordi-backed Azure subscription and isolated
`hinterland-dev-rg`. Never target `gordi-pilot-rg`. Public/runtime resources
use the `HINTERLAND_` settings contract and the `hinterland` kid-auth path.

Record subscription, resource group, image digest, Alembic revision, API
revision, job executions, and smoke request IDs for each promotion. Never put
SAS URLs, child photos, manual child text, or raw coordinates in tickets.

## Public And Authenticated Smoke

```bash
curl -fsS https://api.thehinterlandguide.app/health
curl -fsS https://api.thehinterlandguide.app/ready
curl -fsS https://api.thehinterlandguide.app/.well-known/hinterland-kid-jwks.json
```

The protected promotion also sends an `OPTIONS /v1/auth/consent` preflight to
both the exact Container Apps revision URL and the public API domain. Each must
echo the requesting origin for both trusted parent surfaces
(`parents.thehinterlandguide.app` and the current parent Static Web Apps
domain), allow credentials, `POST`, and `content-type`, and must never use a
wildcard origin. This gate runs before the authenticated family smoke so a
browser-only consent outage cannot pass promotion again.

An operator can reproduce the non-mutating public-domain probe without a
bearer, email, nonce, or consent record:

```bash
curl -sS -D - -o /dev/null -X OPTIONS \
  https://api.thehinterlandguide.app/v1/auth/consent \
  -H "Origin: https://parents.thehinterlandguide.app" \
  -H "Access-Control-Request-Method: POST" \
  -H "Access-Control-Request-Headers: content-type"
```

Repeat for both trusted parent origins and both API surfaces. The unrelated
origin negative control must return 400 without
`Access-Control-Allow-Origin`. `http://localhost:19006` is local-development
compatibility and never counts as deployed W1 browser evidence.

### Parent authentication callback gate

The parent web export must contain a real `auth/callback.html`. Azure Static
Web Apps is configured with one exact rule: `GET /auth/callback` rewrites to
that file. There is deliberately no broad `navigationFallback`; an unknown
parent path must remain a 404. This turns a missing callback route into a visible
release failure instead of serving a generic shell that cannot finish MSAL.

CI runs the source contract, and the parent deployment runs the exported
contract before upload. The live parent deployment and protected promotion
both hard-reject any ref other than `refs/heads/main`; a manually dispatched
feature branch must never replace the OAuth/consent surface. After deployment,
the reusable callback verifier sends
an unauthenticated safe probe to both of these origins:

- `https://parents.thehinterlandguide.app`
- `https://purple-coast-088e6b30f.7.azurestaticapps.net`

Each origin must serve the current commit marker and return a direct `200` with
a `text/html` content type for `/auth/callback?route_probe=...`, with no
redirect, the expected route sentinel, `Cache-Control: no-store`,
`Referrer-Policy: no-referrer`, and
`X-Content-Type-Options: nosniff`. Both callback bodies must hash identically.
The protected promotion runs this gate before API rollout and repeats it after
the final web-marker recheck. Neither gate may skip or continue on error.

The route probe contains only the synthetic `route_probe` value. Never place a
real OAuth authorization code, state, token, cookie, email, consent proof, full
callback URL, or response body in a command line, log, screenshot, HAR, or
promotion artifact. Sanitized evidence may contain only the commit, origin
labels, statuses, header pass facts, and callback artifact hash.

After the automated gates pass, complete the browser smoke in a fresh browser
context: `/consent` -> record consent -> Entra sign-in -> `/auth/callback` ->
authenticated `/v1/me` -> `/classroom`. Do not reuse an authorization code from
a failed or earlier attempt. A callback 404/redirect, an unhandled login result,
a failed canonical `/v1/me`, or failure to reach the classroom is a hard W1
promotion stop.

The authenticated parent/kid smoke requires an operator-provided test-parent
Entra v2 access token for `api://hinterland-api/user.access`. The requested
scope uses that URI, while the token's `aud` claim must be the API client ID
`7dd9da3c-b7d6-45d4-955b-d7561c43f209`:

```bash
HINTERLAND_API_BASE_URL=https://api.thehinterlandguide.app \
HINTERLAND_SMOKE_ENTRA_BEARER="<access-token>" \
python scripts/smoke_azure_parent_kid.py
```

The protected promotion additionally requires at least 900 seconds of token
lifetime before the authenticated stage. Its throwaway kid seeds 50 mixed
dispatcher samples, then the workflow polls exact-revision Log Analytics and
fails closed unless every sample is complete and nearest-rank p95 is below
300 ms. The resulting artifact contains aggregate timings and bounded request
IDs, never SAS URLs, coordinates, image bytes, or child text.

It records the exact current consent version with an in-memory 256-bit nonce,
then requires parent signup to present that exact receipt/proof and link it to
the canonical adult. The raw nonce is never written to logs or evidence. A
successful family-group and kid creation proves the server gate. The smoke then
exchanges the kid handoff, calls
`/v1/me`, verifies starter Expedition visibility, and passes the throwaway kid
session in memory to the Observation W1 canary. The optional
`HINTERLAND_SMOKE_EVIDENCE_PATH` receives sanitized request IDs and pass facts.
Do not store a long-lived kid smoke token.

## Migrations-First Deployment

Use `.github/workflows/deploy-azure-api-dev.yml` for ordinary development
deployments. It preserves migration-first immutable-image behavior but is not a
promotion gate. W1 promotion uses the manually dispatched, protected
`.github/workflows/observation-w1-promotion.yml`; its authenticated tests and
alert verification are mandatory and never green-skip missing credentials.

The required order is:

1. apply W1 flags, delete iNaturalist jobs, verify no inherited iNaturalist-queue
   roles, and discover/remove moderation subscriptions from every Event Grid
   system topic sourced by the photo storage account;
2. build from the repository root and resolve one immutable
   `repository@sha256:...` digest;
3. pin only the read-only preflight and migration jobs to that digest;
4. run the Observation duplicate/counter/location preflight, then start the
   migration job and wait for
   `alembic upgrade head` to succeed;
5. pin every consumer and scheduled job to the same digest;
6. ingest the checked-in taxonomy catalog and sync Expedition content;
7. run a bounded `hinterland-state-rebuild` before exposing the new API;
   dispatcher replay also excludes users with queued/running rebuilds;
8. update the Central primary API and East rollback API only after migration
   and required rebuild success, using the same digest as every East job;
9. require the Central generated hostname and public API hostname to report the
   exact current version, while the East rollback hostname remains healthy;
   and
10. run public readiness and exact parent-browser CORS preflights, followed by
   authenticated, Observation, privacy, and worker canaries.

The root build context is mandatory because the image is also the Expedition
content version. `job start --image` is not a substitute for `job update`: a
start-time override can replace template environment/command configuration.

The W1 promotion workflow requires and pins the current W1 job inventory to the
same digest as both API apps. The canonical API is
`hinterland-api-central` in Central US; `hinterland-api` remains the East US
rollback, and the 13 jobs remain only in East US. Deployment must fail if an
API or job drifts from that placement or digest. It does not restore retired platform scripts, legacy
environment aliases, or the retired legacy-reconcile job. The optional
photo-revocation replay job belongs to closed-beta provisioning.

Submission-key columns remain nullable only for the migration-first window.
The API always writes them and recovery jobs register only verified canonical
photos for relay.

If preflight finds migration-managed duplicate photo/review/counter repair,
review the JSON and rerun with the exact report acknowledgement token. Duplicate
submission keys are hard blockers and cannot be waived. Never edit an applied
Alembic revision.


## Local Database And Migrations

```bash
make dev-db
make db-migrate
powershell -File scripts/verify_observation_postgres.ps1
```

The Observation verification script runs migrations and concurrency,
failure/replay, review race, rebuild, a bounded Unknown-path SQL budget, and a
mixed real-handler dispatcher-p95 probe against disposable PostgreSQL 16. The
protected workflow separately verifies deployed Azure p95; localhost timing is
not accepted as release evidence.

## W1 Internal Testing Configuration

Effective configuration must remain:

```text
HINTERLAND_MODERATION_PROVIDER=noop
HINTERLAND_INAT_CV_ENABLED=false
HINTERLAND_INAT_CV_DISCLOSURE_APPROVED=false
HINTERLAND_INAT_CV_BENCHMARK_APPROVED=false
HINTERLAND_INAT_SUBMIT_ENABLED=false
HINTERLAND_OBSERVATION_IDEMPOTENCY_REQUIRED=true
```

Set the active `HINTERLAND_` settings. The revision requires explicit CV gates,
so token absence is not the only permanent control. Delete or disable the
iNaturalist consumer and replay jobs, and revoke direct or inherited runtime
access to the inert submit queue while preserving stale work without processing.

Enumerate all Event Grid system topics whose `source` is the photo storage
account; Azure-generated topic names are not stable. Confirm none of their
subscriptions targets the moderation queue. The
`moderation_outbox` relay is the sole producer. NoOp must end in
`pilot_private`, never `clean`.

Bootstrap enables the repaired consumer before the relay and drains the active
queue. Stale BlobCreated envelopes are dead-lettered without provider egress;
attached observations are independently recovered from PostgreSQL into the
outbox. A nonzero active count after the bounded drain blocks deployment. Review
the printed DLQ count and verify its alert before promotion.

## Observation Canary

Run with a test kid token against the deployed API:

```bash
HINTERLAND_API_BASE_URL=https://api.thehinterlandguide.app \
HINTERLAND_SMOKE_BEARER="<test-kid-token>" \
python scripts/smoke_observation_w1.py
```

The canary verifies BlockBlob upload headers, verified finalization, identical
presign/create replay, persisted reward equality, exactly one observed-order
Journal entry, child DTO minimization, NoOp-to-`pilot_private`, and signed-photo
denial. A changed request under the same idempotency key must return 409.

The exact Play Internal AAB must additionally pass airplane-mode capture, kill
after PUT, relaunch/reconnect, lost-create-response, account switch, catalog vs
manual/Unknown, no-location, and no-raw-coordinate inspection.

If any code, configuration, or content changes after the promoted commit or
exact AAB is built, that AAB and its device evidence are invalid. Merge the fix,
rerun the protected server promotion, build/publish a new versioned AAB, and
repeat the required physical-device runs.

## Photo Access Probe

Probe owner child, peer child, unrelated user, managing adult, and reviewer.
Expected access:

- clean: owner and authorized managing adult/reviewer;
- quarantine: authorized adult reviewer only;
- pending, pilot-private, failed, rejected, or deleted: nobody receives a URL;
- peer children: never.

A URL outside this matrix is a stop-pilot privacy incident. Rotate affected
credentials only after preserving request IDs and state evidence.

## Moderation Outbox And Worker

Scheduled W1 jobs include:

- `hinterland-mod-outbox-relay`
- `hinterland-moderation-job`
- `hinterland-legacy-reconcile` (temporary cutover guard)
- `hinterland-dispatcher-replay`
- `hinterland-state-rebuild`
- `hinterland-obs-retention`
- `hinterland-obs-health`
- `hinterland-sweep-stale-reviews`
- `hinterland-rarity-refresh`
- `hinterland-expedition-funnel` (manual evidence job)

W1 runs NoOp; pilot-private bytes receive no URL and purge after seven days.
Before closed beta, staging must prove strict four-category Content Safety
validation, duplicate delivery/lease expiry, verified destination copy,
database failure after copy, retry, and DLQ. Never delete a source while Azure
copy remains pending.

## Dispatcher Recovery

`dispatcher.complete` logs `duration_ms`. Replay claims only pending,
failed, or blocked handler rows with `FOR UPDATE SKIP LOCKED` and the same user
advisory lock as finalization/rebuild.

```bash
cd backend
uv run python -m admin.dispatcher_replay
```

Do not call the submission endpoint, hand-edit counters, or fabricate
predecessor state. The saved Observation remains visible with
`dispatch_status=pending|partial` while rewards catch up.

## Review And Rebuild Recovery

Approve/reject/stale-review must converge through the shared review service so
only one actor resolves a row. Rejection tombstones and queues the per-user
rebuild; it never decrements selected counters in place.

The rebuild coalesces by user, acquires the user lock, and replaces counters,
Dex, Expedition contribution, Sanctuary, handler ledgers, and rewards in one
transaction. It retries five times. For terminal failure, preserve error and
Observation IDs, correct the cause, and explicitly requeue. Never repair only
one projection.

After a canary rejection, verify the photo is inaccessible and replacement
first-find/Expedition/Sanctuary state is consistent. Rebuild emits no
celebration or notification.

## iNaturalist Egress

Public submission stays disabled for W1 and closed beta. Optional post-clean CV
also stays disabled until disclosure/legal approval and the reviewed 50-image
benchmark. A configured token is not permission to enable either feature.

If any gate flips unexpectedly:

1. stop/disable producer and consumer jobs;
2. restore both feature gates to false;
3. dead-letter or quarantine queued work without processing it; and
4. determine whether any photo left Azure and begin the privacy incident path.

Never purge evidence before reconciliation.

## Mobile Internal Pilot Gate

```bash
cd mobile
npm ci
npm run typecheck
npm test -- --runInBand
APP_ENV=play-internal npm run config:play-internal
```

Verify `app.thehinterlandguide`, display name **The Hinterland Guide Internal**,
update channel `play-internal`, fine location blocked, coarse foreground only,
and the current Hinterland API URL. Then install the exact AAB through Play
Internal and run the physical-device pilot script.

The rebranded package is a deliberate fresh sandbox. Before an older install is
retired, inventory its owner-scoped SQLite queue and reconcile each item to a
canonical server Observation or explicitly discard it with the adult. Keep the
old install until that is complete. Record `zero old installs` when applicable;
do not treat silence as evidence. See
[`observation-w1-promotion.md`](observation-w1-promotion.md#fresh-package-cutover).

## Alerts And Closed-Beta Gate

Apply and verify `infra-azure/observation-w1-monitoring.sh`, then send its safe
action-group test and confirm receipt. It covers:

- moderation/pending-photo age and moderation DLQ;
- rebuild backlog, five-attempt failure, and duration;
- dispatcher partial/blocked backlog and p95;
- idempotency conflicts and state mismatches;
- retention backlog; and
- Observation job failures.

The protected promotion workflow publishes only sanitized operational
evidence. Action-group API acceptance is recorded automatically; an adult must
separately record that the notification arrived.

Closed beta requires one digest everywhere, outbox-only producer, real Content
Safety safe/flagged/unavailable/malformed probes, review/rebuild canary, and 24
hours or 25 submissions with zero duplicates, unauthorized reads, or stuck
work.

## Account Deletion

```bash
curl -X DELETE \
  -H "Authorization: Bearer <token>" \
  https://api.thehinterlandguide.app/v1/me
```

Immediate effect disables the user and invalidates auth cache. Full linked data
and Blob erasure follows the reviewed asynchronous workflow/retention policy.

## Incident Triggers

Hard-stop W1 for wrong-user data, unauthorized photo URL, pre-clean/external
photo egress, duplicated/lost retry work, raw coordinate leakage, incorrect
consent, account-switch presentation leakage, or wildcard/unexpected-origin
browser API access. Preserve evidence and follow
`android-internal-pilot-stop-plan.md`.

## Runtime AI Violation

Any live LLM or multi-agent call from a kid-facing request path violates ADR
0002/0007. Roll back, identify the import/call path, add a regression test, and
only reopen with a new ADR.
