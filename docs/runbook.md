# Hinterland Azure Runbook

ADR 0010 makes Azure the active runtime. GCP Cloud Run/Cloud SQL/Cloud Tasks
instructions are historical unless explicitly called out as residual DNS or
Firebase Hosting work.

## Public Smoke

```bash
curl -fsS https://api.dragonfly-app.net/health
curl -fsS https://api.dragonfly-app.net/ready
curl -fsS https://api.dragonfly-app.net/.well-known/dragonfly-kid-jwks.json
```

Expected:

- `/health` returns `{"status":"ok", ...}`.
- `/ready` returns 200 when required dependencies are configured.
- JWKS returns at least kid `k1-2026-06` until the first key rotation.

The public landing/support/legal site has its own deploy and smoke checklist in
[`landing-deploy-runbook.md`](landing-deploy-runbook.md). Use it after landing
PRs merge and before putting `https://dragonfly-app.net` URLs into Google Play
Console fields.

## Authenticated Parent/Kid Smoke

The Azure smoke does not automate Entra interactive sign-in. Supply an Entra
access token for a test parent via env var:

```bash
DRAGONFLY_API_BASE_URL=https://api.dragonfly-app.net \
DRAGONFLY_SMOKE_ENTRA_BEARER="<access-token>" \
python scripts/smoke_azure_parent_kid.py
```

Flow:

1. `POST /v1/auth/consent`
2. `POST /v1/auth/parent-signup`
3. `POST /v1/groups`
4. `POST /v1/groups/{group_id}/kids`
5. `POST /v1/auth/kid-exchange`
6. `GET /v1/me` as the kid

The legacy `scripts/smoke_phase4.py` is Firebase-era only. Do not use it as a
deploy gate for the Azure runtime.

## Deploy API Dev

GitHub Actions workflow: `.github/workflows/deploy-azure-api-dev.yml`.

Required GitHub secrets:

- `AZURE_CLIENT_ID`
- `AZURE_TENANT_ID`
- `AZURE_SUBSCRIPTION_ID`
- optional `DRAGONFLY_SMOKE_ENTRA_BEARER`

Workflow shape:

1. Authenticate with Azure federated identity.
2. Build backend image in ACR `dragonflyacrdev` (build context = repo root so
   `content/expeditions/` ships inside the image).
3. Update Container App `dragonfly-api` in resource group `dragonfly-dev-rg`.
4. Run `alembic upgrade head`.
5. Point the `dragonfly-sync-expeditions` Container Apps Job at the new image,
   then start it.
6. Smoke public probes.
7. Run authenticated smoke when the token secret is configured.

Manual deploy (run from the repo root — the build context must be the repo
root so the expedition content ships inside the image):

```bash
az acr build \
  --registry dragonflyacrdev \
  --image dragonfly-api:<git-sha> \
  --file backend/Dockerfile \
  .

az containerapp update \
  --name dragonfly-api \
  --resource-group dragonfly-dev-rg \
  --image dragonflyacrdev.azurecr.io/dragonfly-api:<git-sha>

az containerapp job update \
  --name dragonfly-sync-expeditions \
  --resource-group dragonfly-dev-rg \
  --image dragonflyacrdev.azurecr.io/dragonfly-api:<git-sha>

az containerapp job start -n dragonfly-sync-expeditions -g dragonfly-dev-rg
```

Run the sync job after every deploy: the image IS the expedition content
version, and the job materializes `/app/content/expeditions` into Postgres.
The job's template pins whatever image it was provisioned with, so the
`az containerapp job update --image` step is mandatory — starting the job
without it re-syncs the previous image's content. Do not pass `--image` on
`job start` instead: start-time container overrides replace the whole
template, dropping the job's env vars and command.

The old Cloud Run workflow is a manual no-op. If a Cloud Run service was
accidentally recreated, delete it only after the no-op workflow has landed.

```bash
gcloud run services delete dragonfly-api \
  --project dragonflyapp-495423 \
  --region us-central1
```

## Local Database And Migrations

```bash
make dev-db
make db-migrate
```

Deployed migrations should run from the controlled Azure deploy workflow or a
one-off operator shell using the same `DRAGONFLY_DATABASE_*` settings as the
Container App. Do not add app-startup migrations.

## Mobile Internal Pilot Gate

Before uploading an AAB to Google Play Internal Testing:

```bash
cd mobile
npm ci
npm run typecheck
APP_ENV=play-internal npm run config:play-internal
```

The config check must verify:

- package `com.dragonfly.app`
- display name `Hinterland Internal`
- update channel `play-internal`
- `ACCESS_FINE_LOCATION` blocked
- `ACCESS_COARSE_LOCATION` explicitly requested

Then run the physical-device script in
[`android-internal-pilot-test-script.md`](android-internal-pilot-test-script.md).
For `play-internal` and production, native adult setup is web-first through
`https://parents.dragonfly-app.net`; kids sign in through the native QR handoff
screen.

## Moderation

W1 Internal Testing may run with `DRAGONFLY_MODERATION_PROVIDER=noop` and iNat
submission disabled. Closed beta must wire Azure async moderation first.

Target closed-beta shape:

- Blob `pending/` event or explicit queue message starts moderation.
- Worker calls Azure AI Content Safety.
- Clean photos move to `observations/`.
- Flagged photos move to `quarantine/` and create a `review_queue` row.
- Provider outage holds/retries pending and does not default-allow.

Adult review actions:

- `approve`: move/copy to `observations/`, resolve review, allow downstream iNat submit.
- `reject`: mark photo deleted, resolve review, decrement observation counter.

## iNaturalist Submit

iNat submission stays off until risk 0001 is closed: project account/OAuth token
obtained and the 50-photo CV benchmark is run.

Closed-beta target:

- clean moderation path enqueues iNat submit
- idempotency key = Hinterland observation id
- retries with DLQ/dead-letter visibility
- terminal failures alert but never affect the kid submission response

**Flipping `DRAGONFLY_INAT_SUBMIT_ENABLED` OFF while messages are queued:**
producers stop writing outbox rows and the consumer's startup guard refuses
to submit, but already-queued `inat-submit` messages just sit there — the
queue has no message TTL, KEDA keeps spawning no-op worker executions, and
the matching `inat_submit_outbox` rows stay `pending`/`enqueued` with no
alert. After a deliberate flip-off, drain the queue explicitly (dead-letter
or purge via `az servicebus queue`) and reconcile the outbox rows so the
state is visible instead of silently stranded.

## Dispatcher Replay And p95

Dispatcher logs `dispatcher.complete` with `duration_ms`. Use Log Analytics to
measure p95 once at least 50 real observations exist.

Replay crashed/un-dispatched observations through:

```bash
cd backend
uv run python -m admin.dispatcher_replay
```

Scheduled Azure jobs should eventually run:

- `python -m admin.rarity_refresh`
- `python -m admin.sweep_stale_reviews`
- `python -m admin.dispatcher_replay`

## Account Deletion

The app-visible endpoint is:

```bash
curl -X DELETE \
  -H "Authorization: Bearer <token>" \
  https://api.dragonfly-app.net/v1/me
```

Immediate effect: sets `users.disabled_at`, busts the auth cache, and returns
`{"status":"deletion_requested", ...}`. Full erasure of linked child data,
photos, and any iNaturalist project-account contribution remains an operator
follow-up until the legal policy is finalized.

## Incident Triggers

Hard stop the pilot and follow `android-internal-pilot-stop-plan.md` if any of
these appear:

- auth failure exposing wrong user data
- child can reach public chat or social features
- photo appears publicly without a moderation decision
- submit crash loses kid data
- incorrect consent state
- location/privacy surprise

## Runtime AI Violation

Any live LLM or multi-agent call from a kid-facing request path violates ADR
0002/0007. Roll back, identify the import/call path, add a regression test, and
only reopen with a new ADR.
