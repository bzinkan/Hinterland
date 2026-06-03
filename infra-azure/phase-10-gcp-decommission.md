# Phase 10 -- GCP decommission record

Date: 2026-06-03. This is a written record of the GCP-side decisions that
land alongside the migration to Azure. Sister document to ADR 0010.

## Scope cuts vs the ADR

ADR 0010 Phase 10 originally listed: disable Cloud Run, snapshot + delete
Cloud SQL, delete GCS bucket, disable Firebase Hosting, delete Cloud DNS
zone. The decommission that actually shipped is narrower and pragmatic.

| ADR action | What actually shipped | Why |
|---|---|---|
| Disable Cloud Run | **Deleted** `dragonfly-api` service | Image still in Artifact Registry; trivially recreatable. |
| Snapshot + delete Cloud SQL | **Stopped** `dragonfly-postgres-dev` (activationPolicy=NEVER) | Stop preserves data + automated backups, zero compute cost (~$15-25/mo saved). Reversible with one `gcloud sql instances patch --activation-policy=ALWAYS`. Delete is irreversible; not worth the risk for $0 additional savings. |
| Delete GCS bucket | **Kept** `dragonfly-photos-dev-dragonflyapp-495423` as-is | Empty/near-empty bucket; storage cost is ~$0. No reason to incur the migration cost of azcopy + lifecycle juggling for a beta bucket. Phase 11 (if ever) deletes it after a 30-day retention window. |
| Disable Firebase Hosting parents site | **Kept** `dragonfly-parents-dev` live | Apex + www of the public domain still need a host; Azure SWA apex requires Azure DNS specifically and we kept Cloud DNS as authoritative. Firebase Hosting Free tier is $0. See "Persistent Firebase footprint" below. |
| Delete Firebase Auth tenant | **Kept** the project | Phase 7 MSAL bundler issue (msal-common exports map) means the mobile app still uses Firebase Auth for parent sign-in on every platform. Firebase Auth Free tier is $0. Phase 11 candidate. |
| Delete Cloud DNS zone | **Kept** `dragonfly-app-zone` as authoritative | Holds the records for the apex + www (Firebase Hosting) + api + parents (Azure). Cost ~$1/mo. Moving to Azure DNS requires a registrar repoint + apex + www re-cert and isn't worth the lift for the beta. |

## What actually changed

Deleted in GCP:
- `gcloud run services delete dragonfly-api --region=us-central1` -- the Cloud Run service is gone.

Stopped in GCP:
- `gcloud sql instances patch dragonfly-postgres-dev --activation-policy=NEVER` -- the instance shows STOPPED, no compute charge.

Kept in GCP (intentionally):
- Cloud SQL instance shape + data + backups (for restart)
- GCS bucket (photos)
- Firebase Hosting sites: `dragonfly-landing-dev` (apex + www), `dragonfly-parents-dev` (now unused, but kept as standby)
- Firebase Auth tenant `dragonflyapp-495423`
- Cloud DNS zone `dragonfly-app-zone`
- Artifact Registry images

## Persistent Firebase footprint (apex + www)

The apex `dragonfly-app.net` and `www.dragonfly-app.net` are still served by
Firebase Hosting site `dragonfly-landing-dev`. Azure Static Web Apps
supports custom apex domains only when Azure DNS is the authoritative
nameserver; we kept Cloud DNS because:

- It's $1/mo;
- All records (api + parents on Azure, apex + www + email on Firebase / GCP)
  are already there;
- Migrating the zone would require a registrar-side NS change + cert
  reissue on every record, for marginal benefit.

If/when Azure DNS migration becomes worthwhile, the path is:
1. Recreate the zone in Azure DNS, import all records.
2. Update registrar NS records to point at Azure DNS nameservers.
3. Claim `dragonfly-app.net` + `www.dragonfly-app.net` as apex/subdomain
   on `dragonfly-landing-swa`.
4. Delete Firebase Hosting sites.

## Cost delta

| Line item | Before | After |
|---|---|---|
| GCP Cloud Run (idle) | ~$0 | $0 (deleted) |
| GCP Cloud SQL B1ms | ~$15-25/mo | $0 (stopped) |
| GCP Cloud Storage (~10GB) | ~$0.20/mo | $0.20/mo (kept) |
| GCP Cloud DNS | ~$1/mo | $1/mo (kept) |
| GCP Firebase Hosting / Auth | $0 (free) | $0 (kept) |
| **GCP total** | **~$17-27/mo** | **~$1.20/mo** |
| Azure Postgres B1ms | $0 | ~$15-25/mo |
| Azure Container Apps (min=0) | $0 | $0-25/mo |
| Azure Storage / KV / ACR / SWA | $0 | ~$6/mo |
| Azure Content Safety F0 | $0 | $0 (free 5k/mo) |
| **Azure total** | **$0** | **~$25-55/mo** |

Net new spend ~$10-30/mo above the previous GCP-only baseline. $1000 credit
covers ~25-40 months at this profile. Well within bounds.

## Reversal procedure (if anything in Azure proves unworkable)

1. Restart Cloud SQL: `gcloud sql instances patch dragonfly-postgres-dev --activation-policy=ALWAYS`.
2. Recreate Cloud Run from the artifact-registry image: `gcloud run deploy dragonfly-api --image us-central1-docker.pkg.dev/dragonflyapp-495423/dragonfly/dragonfly-api:latest`.
3. Repoint `api.dragonfly-app.net` CNAME from the Container Apps FQDN back to `ghs.googlehosted.com.`
4. Repoint `parents.dragonfly-app.net` CNAME from the SWA hostname back to Firebase Hosting (199.36.158.100 A record).
5. Container App env var `DRAGONFLY_STORAGE_PROVIDER=gcs` + `DRAGONFLY_MODERATION_PROVIDER=cloud_vision_safesearch` flips the backend back to GCP services -- the auth path still uses Entra, but the rest of the backend works against the GCP datastore.

The migration is rollback-friendly through Phase 11 (Firebase Auth removal).

## Phase 11 outcomes

| Item | Disposition |
|---|---|
| Real MSAL hookup on the parents web bundle | **DONE** in PR #88 (Phase 11a). Metro `resolveRequest` shim fixes the `@azure/msal-common/browser` package layout; `PublicClientApplication.initialize()` runs on every web load; `acquireTokenSilent` writes the access token to bearer storage. |
| Sign-in.tsx UI rewrite | **DONE** in PR #88. Web shows a single "Continue with Microsoft" button (`Platform.OS === "web"` branch). Native iOS/Android keep the email/password Firebase form. |
| Drop `firebase-admin` + `google-cloud-storage` from backend deps | **DONE** in PR #89 (Phase 11b). 10 transitive deps also removed; backend image is ~50 MB smaller. `python-jose` removed too -- pyjwt covers everything. |
| Drop `GcsSignedUrlGenerator` + `CloudVisionSafeSearchModerator` | **DONE** in PR #89. ~700 net lines removed. `storage_provider` Literal trimmed to `["noop", "blob"]`; `moderation_provider` trimmed to `["noop", "azure_content_safety"]`. |
| Delete `backend/admin/cleanup_smoke_users.py` | **DONE** in PR #89. Was the only remaining `firebase_admin` import outside tests. |
| **Migrate Cloud DNS -> Azure DNS + cut apex/www over to Static Web Apps** | **DEFERRED INDEFINITELY** (see below). |
| Delete Cloud SQL instance + GCS bucket after 60-day soak | Pending (60-day soak window). |
| Delete the Firebase project entirely | Pending (blocked on apex/www staying on Firebase Hosting). |

## Why apex/www stays on Firebase Hosting (decision: indefinite)

Azure Static Web Apps' Free tier does not officially support apex domains
when authoritative DNS is anywhere other than Azure DNS -- the documented
path is "ALIAS record in Azure DNS." A workaround using plain A records
on a third-party DNS is possible but unsupported and brittle (the SWA
underlying IP isn't pinned across rebuilds). Empirically, the Phase 9
attempt at the apex SWA claim through Cloud DNS hung indefinitely; the
Phase 11c re-attempt at `www` via cname-delegation returned `BadRequest:
CNAME record is invalid` without ever exposing a usable validation
token.

The realistic path to "everything on Azure" requires:

1. Recreate the zone in Azure DNS, importing all records (MX / SPF / DKIM
   for email, NS, SOA, plus the 4 record sets we manage).
2. Update the registrar's NS records to point at Azure DNS nameservers.
3. Wait 1-48h for global NS propagation. **Risk: any record-set drift
   between Cloud DNS and Azure DNS during this window takes the domain
   dark.**
4. Claim `dragonfly-app.net` (apex) + `www.dragonfly-app.net` on
   `dragonfly-landing-swa` once the new NS records are authoritative.
5. Add the SWA-provided TXT / ALIAS records in Azure DNS.
6. Wait for managed-cert issuance + propagation.
7. Delete the Firebase Hosting site `dragonfly-landing-dev`.

That's roughly an afternoon of work with a real downtime risk and no
functional payoff -- everything that needs to be on Azure already is.
The residual GCP footprint is **~$1.20/mo** (Cloud DNS zone + ~10 GB
Cloud Storage). Burning a developer afternoon and the downtime risk to
save that is not a positive trade-off.

If a future change forces it -- e.g. Microsoft moves apex SWA support
behind paid tiers, or we decide to delete the entire Firebase project
for COPPA/audit reasons -- the steps above are the recipe.

Until then: api + parents on Azure, apex + www on Firebase Hosting via
Cloud DNS is the stable end state.
