# ADR 0010: Azure target architecture (supersedes ADR 0005)

- **Status:** Accepted
- **Date:** 2026-06-02
- **Deciders:** Solo author
- **Supersedes:** ADR 0005 (GCP target architecture), ADR 0008 (Public Cloud Run with Firebase enforcement), ADR 0009 (Moderation provider: Cloud Vision SafeSearch)
- **Related:** ADR 0002 (LLMs are author-time, not runtime), ADR 0014 (Firebase/GCP decommission)

## Context

ADR 0005 picked GCP as the runtime. The full beta surface (Phases 0-11 + web follow-ups 1-5) was built and verified on GCP between 2026-05-05 and 2026-06-01. `https://parents.hinterland-app.net` and `https://hinterland-app.net` were part of that historical deployment.

Two facts changed the calculation:

1. **GCP startup-credit application was denied.** AWS Activate is in progress but uncertain. Microsoft for Startups granted a verified $1000 Azure credit (expires 2026-08-31). Without credit subsidy, monthly burn at zero traffic is real money.
2. **The build is mostly cloud-thin.** The dispatcher, matchers, expedition models, inat client, ulid keys, and SQL schema are cloud-agnostic. At the time of the migration decision, cloud coupling was concentrated in five files: `app/core/auth.py` (Firebase Admin), `app/core/storage.py` (GCS), `app/moderation/provider.py` (Cloud Vision), `mobile/src/auth/firebase.ts` (Firebase Web SDK), and the deploy YAMLs.

A migration is feasible because the cloud surface is small. The decision is to take the $1000 credit, move now while the codebase is still 4 weeks old, and use the migration as a forcing function to harden the cloud abstraction.

The non-negotiable invariants in AGENTS.md are cloud-agnostic and unchanged. This ADR is about *how* they are implemented on Azure.

## Decision

Use the following Azure services for the Phase 1 target architecture, in subscription `5a04114f-9102-4e0b-828b-b385096edfbc` (tenant `3b7e8876-fd7e-4b71-b14f-f1bf9beb8e05`), resource group `hinterland-dev-rg` in region `eastus2`:

### 1. Auth: Microsoft Entra External Identities (CIAM)

Replaces Firebase Auth.

Parents and teachers sign in with email + password against an Entra External Identities tenant (separate from the management tenant). Kids do not sign in directly — the parent provisions a kid account via the Admin API, which mints a one-time signed handoff token (a JWT signed by the backend with a project secret). The kid app exchanges the handoff token at `POST /v1/auth/kid-exchange` for an Entra access token issued via the OAuth 2.0 Token Exchange flow (RFC 8693) using a confidential client.

This replaces Firebase's `signInWithCustomToken` flow. The shape is the same — adult provisions, kid scans QR, kid device gets a session — but the token mechanics change.

The FastAPI dependency in `app/core/auth.py` is rewritten to verify Entra ID tokens against the Entra JWKS endpoint. Custom claims (`role`, `group_id`) are issued via Entra's claims-mapping policy or as backend-augmented claims on first request.

Rejected: Auth0 (third-party, not in the $1000 credit umbrella), Azure AD B2C (deprecated for new tenants in favor of External Identities), rolling our own JWT (re-implements verified-email + password reset).

### 2. Datastore: Azure Database for PostgreSQL Flexible Server

Replaces Cloud SQL for PostgreSQL.

One Burstable B1ms Flexible Server in `hinterland-dev-rg` holds the same schema, migrated via `pg_dump` from the Cloud SQL dev instance. Public access with firewall rules in dev; private endpoint deferred to prod unless data residency or compliance requires it sooner.

The SQLAlchemy + asyncpg + Alembic stack is unchanged. Only the connection URL changes (driver, host, sslmode). Alembic migrations run as-is.

Rejected: Cosmos DB for PostgreSQL (overprovisioned for our scale), Single Server (deprecated), Container Apps sidecar Postgres (operational burden, no managed backups).

### 3. Object storage: Azure Blob Storage

Replaces Cloud Storage.

One Storage account `hinterlandphotosdev` (lowercase, alphanumeric — Azure account names have tight constraints) with a private container `photos`. Photo uploads use user-delegation SAS URLs (the SAS equivalent of GCS signed URLs); SafeSearch quarantine writes to the same container with a different prefix.

Photos in the existing GCS bucket are migrated via `azcopy` with a GCS service-account key. After cutover the GCS bucket is set to read-only for 7 days, then deleted.

The `SignedUrlGenerator` protocol in `app/core/storage.py` is unchanged; a new `BlobSignedUrlGenerator` replaces `GcsSignedUrlGenerator`. All call sites already go through the protocol so the changes are concentrated in one class + DI wiring.

Rejected: Azure Files (not object storage), Azure Data Lake Storage Gen2 (overkill).

### 4. Async work: Container Apps + Service Bus (deferred)

Phase 1 keeps the synchronous moderation path (Cloud Tasks + Eventarc were planned for GCP but never wired — see risk 0002). On Azure, the eventual asynchronous moderation path uses Service Bus + Event Grid + Container Apps jobs. None of this is wired in Phase 1 — we keep the synchronous path until the photo volume justifies async.

### 5. Container compute: Azure Container Apps

Replaces Cloud Run.

`hinterland-api` runs on Azure Container Apps with min-replicas = 0, max-replicas = 3, in a Container Apps managed environment in `hinterland-dev-rg`. The image is built from `backend/Dockerfile` (unchanged) and pushed to Azure Container Registry `hinterlandacrdev`.

Rejected: App Service (less suited for containerized workloads), Container Instances (no scale-to-zero), AKS (operational burden for a beta).

### 6. Secret storage: Azure Key Vault + Container Apps secrets

Replaces Secret Manager.

Database passwords, Entra app client secrets, and the handoff-token signing key live in `hinterland-kv-dev`. The Container App reads them via managed identity at startup; runtime env vars never carry plaintext secrets.

### 7. Moderation: Azure AI Content Safety

Replaces Cloud Vision SafeSearch.

The `Moderator` protocol gains an `AzureContentSafetyModerator` implementation calling Content Safety's image analysis endpoint. The category mapping (Adult / Violence / Racy / Medical → quarantine) is rewritten for Content Safety's category set (Sexual / Violence / SelfHarm / Hate; each with a 0-6 severity score). Threshold: any category at severity ≥ 4 quarantines the photo.

Rejected: Custom Vision (requires training data we don't have), third-party (not in credit umbrella).

### 8. Observability: Azure Monitor + Application Insights + Log Analytics

Replaces Cloud Monitoring + structured Cloud Logging.

Container Apps streams stdout/stderr to a Log Analytics workspace; the FastAPI app emits structured JSON logs (`structlog` config unchanged); Azure Monitor alarms replace Cloud Monitoring alarms; the dogfood dashboard from Phase 11 is rebuilt as an Azure dashboard.

### 9. Frontend hosting: Azure Static Web Apps

Replaces Firebase Hosting.

Two Static Web Apps: `hinterland-landing-swa` for `thehinterlandguide.app` + `www.thehinterlandguide.app`, and `hinterland-parents-swa` for `parents.thehinterlandguide.app`. Custom domain wiring uses Azure Static Web Apps managed certificates.

### 10. DNS: Azure DNS

Replaces Cloud DNS.

The active public domains are on `thehinterlandguide.app`. Azure Static Web Apps and Container Apps are the serving targets. ADR 0014 removes the old Cloud DNS/Firebase Hosting rollback posture from active guidance.

### 11. CI/CD: GitHub Actions with federated identity (OIDC)

Replaces GitHub Actions + GCP Workload Identity Federation.

A federated credential on a User-Assigned Managed Identity lets the existing GitHub workflows authenticate without any long-lived secrets, same shape as the GCP WIF setup but via Azure's federated identity (no JSON keys). Workflows: one to build + push to ACR + deploy to Container Apps, one per Static Web App.

## Cost estimate (low-traffic dev)

| Service | Monthly est. |
|---|---|
| Container Apps (min=0, 1 vCPU, 2 GiB) | $25-50 |
| Postgres Flexible Server B1ms (32 GB) | $15-25 |
| Blob Storage (~10 GB) | $1 |
| Container Registry Basic | $5 |
| Static Web Apps × 2 | $0 (free tier) |
| Entra External Identities (< 50k MAU) | $0 |
| Azure DNS (1 zone) | $1 |
| Content Safety (low call volume) | $0-5 |
| Log Analytics (5 GB/mo) | $10 |
| Key Vault (10 ops/day) | $1 |
| **Total** | **~$60-100/mo** |

$1000 credit ≈ 10-16 months of runway at zero traffic. Sufficient through the closed beta and into early public beta.

## Migration phases

Each phase is one PR (or a small group of related PRs) — same cadence as the GCP Phase 0-11 sequence.

| # | Scope | Code change? |
|---|---|---|
| 0 | RG, providers, tags, this ADR, `infra-azure/` skeleton | None |
| 1 | Entra External Identities tenant + app registrations + claims-mapping policy | None |
| 2 | Postgres Flexible Server + pg_dump/restore | None |
| 3 | Blob Storage account + photo migration via azcopy | None |
| 4 | ACR + first image push | None |
| 5 | Container Apps environment + first deploy with Postgres pointed at Azure | Connection string only |
| 6 | Backend code: replace Firebase Admin SDK with Entra verifier (PR 6a), replace GCS storage with Blob (PR 6b), replace Cloud Vision with Content Safety (PR 6c) | Yes |
| 7 | Mobile code: replace Firebase Web SDK with MSAL.js; rewrite kid handoff token exchange | Yes |
| 8 | Static Web Apps for landing + parents; new deploy workflows | Workflow YAML |
| 9 | Azure DNS zone + repoint at registrar; reissue certs via Static Web Apps + Container Apps custom-domain bindings | DNS only |
| 10 | Decommission: disable Cloud Run, snapshot + delete Cloud SQL, delete GCS bucket, disable Firebase Hosting, delete Cloud DNS zone | None |
| 11 | Azure Monitor alarms + dashboard rebuild | None |

## Consequences

- Loses Firebase's mature mobile auth SDKs; gains an Azure-native flow we control end-to-end.
- Adds a second cloud tenant (Entra External Identities is its own tenant separate from the management tenant `brianhinterlandapp.onmicrosoft.com`).
- The handoff-token mechanic gets reimagined; the QR shape from PR #69 stays, the contents change to a backend-signed JWT.
- Five risk docs (0001-0006) continue to apply; new risk 0007 will track the migration cutover.
- The GCP architecture work (5 weeks, ~$0 spent) is not wasted — it validated the shape of the system; Phase 6/7 are mechanical translations.

## Open questions

- Whether to keep Cloud DNS during the migration (probably yes, until Phase 9) or cut over earlier.
- Whether to keep Firebase Auth temporarily as a fallback for emergency rollback (probably no — it adds complexity and the beta is small).
- Whether the kid handoff token needs RFC 8693 token-exchange semantics or a simpler "exchange this opaque token for an Entra session" custom endpoint (leaning custom; full token-exchange is over-engineered for one flow).

---

## Migration completed -- 2026-06-03

Phases 0-10 landed. The full state + scope cuts are documented in
[`infra-azure/phase-10-gcp-decommission.md`](../../infra-azure/phase-10-gcp-decommission.md).
Key deltas vs the ADR plan:

- **Postgres landed in centralus**, not eastus2. This Sponsored subscription's quota blocks Burstable Postgres in eastus + eastus2. The exact W1 workload proved that the East US API could not meet the multi-savepoint dispatcher's 300 ms budget. [ADR 0016](0016-central-us-api-colocation.md) therefore places the primary API in Central US while retaining the East API as a same-digest rollback and keeping the jobs and non-database dependencies in East US.
- **The old Hinterland apex/`www` Firebase fallback is superseded.** ADR 0014 makes Azure Static Web Apps the only active landing/parents hosting path.
- **Cloud SQL was stopped, not deleted.** Activation-policy NEVER preserves data and backups, zero compute cost, instant restart.
- **MSAL is the active adult web auth path.** Native mobile uses kid QR/dev login and parent web handoff; Firebase email/password sign-in is removed by ADR 0014.

### Full decommission addendum -- 2026-07-07

ADR 0014 supersedes the residual-hosting/rollback carve-outs above. Firebase
Hosting/Auth/SDKs and GCP deploy paths are no longer retained as active paths.
The active public domains are `thehinterlandguide.app`,
`www.thehinterlandguide.app`, `parents.thehinterlandguide.app`, and
`api.thehinterlandguide.app`, backed by Azure Static Web Apps and Azure
Container Apps. Old GCP/Firebase resources may remain externally until the ADR
0014 deletion gates are satisfied, but repo/runtime/CI guidance must be
Azure-only.

---

## Phase 1 frozen contract

Resolved decisions from the Phase 1 design review (2026-06-02). These supersede the corresponding open items above and the looser sketches in Section 1 and Section 6.

### Two-path token verifier

The FastAPI auth dependency dispatches on the `iss` claim:

- **Entra path (adults — parents and teachers).** Verify against the CIAM tenant's discovery document:
  - issuer: `https://login.microsoftonline.com/18dbd7fa-c411-49bc-82fc-9ccaa26e3404/v2.0`
  - requested scope: `api://hinterland-api/user.access`
  - audience: `7dd9da3c-b7d6-45d4-955b-d7561c43f209` (the API application's client ID; Entra v2 access tokens always use this GUID in `aud`)
  - authorized client: `60504e4c-6b5f-4031-a80a-3e4bdfae29b2` (`hinterland-client`); require delegated `scp=user.access` so app-only or unscoped tokens fail closed
  - signature: RS256 against Entra's JWKS at `https://login.microsoftonline.com/18dbd7fa-c411-49bc-82fc-9ccaa26e3404/discovery/v2.0/keys`
- **Hinterland path (kids).** Verify against the backend's own JWKS:
  - issuer: `https://api.thehinterlandguide.app`
  - audience: `hinterland-api`
  - signature: RS256 with the kid-handoff RSA-2048 keypair stored in Key Vault; public JWKS served at `/.well-known/hinterland-kid-jwks.json`

Kids never receive an Entra-issued token. This overrides ADR Section 1's RFC 8693 token-exchange sketch — RFC 8693 is over-engineered for one flow, and shipping kid tokens through Entra would require seat-licenses we don't want.

### Option C: backend-augmented claims

Claims `role` and `group_id` are NOT carried in the JWT (neither Entra-issued nor Hinterland-issued). On every request the auth dependency resolves them from Postgres (`users.role`, `users.group_id`) and attaches them to the request context.

A 30-second TTL in-memory per-process cache (`{user_id: (role, group_id, expires_at)}`) keeps the per-request DB hit cheap. A `bust_user_cache(user_id)` hook is called from any code path that mutates `users.role` or `users.disabled_at`, so admin demotions and disablement take effect within one in-flight request rather than on cache expiry.

This resolves ADR Section 1's "claims-mapping policy or backend-augmented claims on first request" open question in favor of backend-augmented, on every request, with the cache. Rationale: avoids the Entra claims-mapping policy round trip and lets us revoke role + disable users without waiting for token refresh.

### Kid handoff flow

1. Parent calls Admin API → backend mints a kid-handoff JWT: RS256, kid header = `k1-2026-07`, issuer `https://api.thehinterlandguide.app`, audience `hinterland-api`, `jti` = random ULID, `exp` = now + 15 minutes, single-use.
2. QR encodes the handoff JWT (same QR shape as PR #69, only the token contents changed).
3. Kid device scans QR, POSTs the handoff JWT to `/v1/auth/kid-exchange`.
4. Backend validates signature + claims, checks `jti` is unused (atomic insert into `kid_handoff_jti`), then issues a session JWT with the same Hinterland-path issuer/audience and a 30-day expiry.
5. Kid app stores the session JWT and uses it as a Bearer token on subsequent requests.

The public verification key is served from `/.well-known/hinterland-kid-jwks.json` so the Hinterland verifier path can fetch + rotate without an out-of-band trust bootstrap.

This overrides ADR Section 6's mention of HS256/Ed25519 for handoff tokens — RS256 with a public JWKS is the chosen shape so the verifier can run the same JWKS-fetch code for both paths.

### Phase 6 schema delta

Alembic migration `add_entra_identity_columns` (lands in Phase 6 PR 6a):

- `users.entra_oid` — `String(64)`, unique, nullable, indexed. Set on first Entra sign-in by looking up by `email` and writing back the `oid` claim. Becomes the primary identity column at Phase 10.
- `users.disabled_at` — `timestamptz`, nullable. When non-null, the auth dependency rejects the request after backend-claim resolution. Used by `bust_user_cache` to force immediate effect.
- `kid_handoff_jti` — new table. Columns: `jti` (text, primary key), `kid_user_id` (FK → `users.id`), `consumed_at` (timestamptz nullable), `expires_at` (timestamptz). Pre-insert with `consumed_at=null` at mint, `UPDATE ... WHERE consumed_at IS NULL` at exchange (atomic single-use). Background sweeper purges rows past `expires_at + 7 days`.
- `users.firebase_uid` is **retained** as a nullable legacy compatibility field until a live-data audit proves it can be migrated or dropped safely. ADR 0014 removes the Firebase Auth rollback path but does not drop this column in the same change.

### Locked identifiers

- `hinterland-api` user.access scope GUID = `1e4fbf5e-8db3-45f3-92c5-8efc6686e75f`. **Do not regenerate.** The pre-authorized application grant on `hinterland-client` references this id, and MSAL clients request the literal scope string `api://hinterland-api/user.access`. Rotating the GUID would force every client to re-consent.
- Kid handoff JWT key id = `k1-2026-07`. The first rotation lands as `k2-...`; the JWKS will return both during the overlap window.

### Override summary

| Earlier ADR statement | Phase 1 resolution |
|---|---|
| Section 1: RFC 8693 token exchange for kids | Kids never get an Entra token. Backend-signed Hinterland-path JWT only. |
| Section 1: claims via Entra claims-mapping policy or backend on first request | Backend-augmented on every request, 30s TTL cache, `bust_user_cache` hook. |
| Section 6: handoff token signed with a project secret (HS-style) | RS256 with public JWKS at `/.well-known/hinterland-kid-jwks.json`. |
| Open question: kid handoff token semantics | Custom `/v1/auth/kid-exchange`, 15-min single-use handoff → 30-day session JWT. |
