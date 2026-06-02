# ADR 0010: Azure target architecture (supersedes ADR 0005)

- **Status:** Accepted
- **Date:** 2026-06-02
- **Deciders:** Solo author
- **Supersedes:** ADR 0005 (GCP target architecture), ADR 0008 (Public Cloud Run with Firebase enforcement), ADR 0009 (Moderation provider: Cloud Vision SafeSearch)
- **Related:** ADR 0002 (LLMs are author-time, not runtime)

## Context

ADR 0005 picked GCP as the runtime. The full beta surface (Phases 0-11 + web follow-ups 1-5) was built and verified on GCP between 2026-05-05 and 2026-06-01. `https://parents.dragonfly-app.net` and `https://dragonfly-app.net` are live.

Two facts changed the calculation:

1. **GCP startup-credit application was denied.** AWS Activate is in progress but uncertain. Microsoft for Startups granted a verified $1000 Azure credit (expires 2026-08-31). Without credit subsidy, monthly burn at zero traffic is real money.
2. **The build is mostly cloud-thin.** The dispatcher, matchers, expedition models, inat client, ulid keys, and SQL schema are cloud-agnostic. Cloud coupling is concentrated in five files: `app/core/auth.py` (Firebase Admin), `app/core/storage.py` (GCS), `app/moderation/provider.py` (Cloud Vision), `mobile/src/auth/firebase.ts` (Firebase Web SDK), and the deploy YAMLs.

A migration is feasible because the cloud surface is small. The decision is to take the $1000 credit, move now while the codebase is still 4 weeks old, and use the migration as a forcing function to harden the cloud abstraction.

The non-negotiable invariants in AGENTS.md are cloud-agnostic and unchanged. This ADR is about *how* they are implemented on Azure.

## Decision

Use the following Azure services for the Phase 1 target architecture, in subscription `5a04114f-9102-4e0b-828b-b385096edfbc` (tenant `3b7e8876-fd7e-4b71-b14f-f1bf9beb8e05`), resource group `dragonfly-dev-rg` in region `eastus2`:

### 1. Auth: Microsoft Entra External Identities (CIAM)

Replaces Firebase Auth.

Parents and teachers sign in with email + password against an Entra External Identities tenant (separate from the management tenant). Kids do not sign in directly — the parent provisions a kid account via the Admin API, which mints a one-time signed handoff token (a JWT signed by the backend with a project secret). The kid app exchanges the handoff token at `POST /v1/auth/kid-exchange` for an Entra access token issued via the OAuth 2.0 Token Exchange flow (RFC 8693) using a confidential client.

This replaces Firebase's `signInWithCustomToken` flow. The shape is the same — adult provisions, kid scans QR, kid device gets a session — but the token mechanics change.

The FastAPI dependency in `app/core/auth.py` is rewritten to verify Entra ID tokens against the Entra JWKS endpoint. Custom claims (`role`, `group_id`) are issued via Entra's claims-mapping policy or as backend-augmented claims on first request.

Rejected: Auth0 (third-party, not in the $1000 credit umbrella), Azure AD B2C (deprecated for new tenants in favor of External Identities), rolling our own JWT (re-implements verified-email + password reset).

### 2. Datastore: Azure Database for PostgreSQL Flexible Server

Replaces Cloud SQL for PostgreSQL.

One Burstable B1ms Flexible Server in `dragonfly-dev-rg` holds the same schema, migrated via `pg_dump` from the Cloud SQL dev instance. Public access with firewall rules in dev; private endpoint deferred to prod unless data residency or compliance requires it sooner.

The SQLAlchemy + asyncpg + Alembic stack is unchanged. Only the connection URL changes (driver, host, sslmode). Alembic migrations run as-is.

Rejected: Cosmos DB for PostgreSQL (overprovisioned for our scale), Single Server (deprecated), Container Apps sidecar Postgres (operational burden, no managed backups).

### 3. Object storage: Azure Blob Storage

Replaces Cloud Storage.

One Storage account `dragonflyphotosdev` (lowercase, alphanumeric — Azure account names have tight constraints) with a private container `photos`. Photo uploads use user-delegation SAS URLs (the SAS equivalent of GCS signed URLs); SafeSearch quarantine writes to the same container with a different prefix.

Photos in the existing GCS bucket are migrated via `azcopy` with a GCS service-account key. After cutover the GCS bucket is set to read-only for 7 days, then deleted.

The `SignedUrlGenerator` protocol in `app/core/storage.py` is unchanged; a new `BlobSignedUrlGenerator` replaces `GcsSignedUrlGenerator`. All call sites already go through the protocol so the changes are concentrated in one class + DI wiring.

Rejected: Azure Files (not object storage), Azure Data Lake Storage Gen2 (overkill).

### 4. Async work: Container Apps + Service Bus (deferred)

Phase 1 keeps the synchronous moderation path (Cloud Tasks + Eventarc were planned for GCP but never wired — see risk 0002). On Azure, the eventual asynchronous moderation path uses Service Bus + Event Grid + Container Apps jobs. None of this is wired in Phase 1 — we keep the synchronous path until the photo volume justifies async.

### 5. Container compute: Azure Container Apps

Replaces Cloud Run.

`dragonfly-api` runs on Azure Container Apps with min-replicas = 0, max-replicas = 3, in a Container Apps managed environment in `dragonfly-dev-rg`. The image is built from `backend/Dockerfile` (unchanged) and pushed to Azure Container Registry `dragonflyacrdev`.

Rejected: App Service (less suited for containerized workloads), Container Instances (no scale-to-zero), AKS (operational burden for a beta).

### 6. Secret storage: Azure Key Vault + Container Apps secrets

Replaces Secret Manager.

Database passwords, Entra app client secrets, and the handoff-token signing key live in `dragonfly-kv-dev`. The Container App reads them via managed identity at startup; runtime env vars never carry plaintext secrets.

### 7. Moderation: Azure AI Content Safety

Replaces Cloud Vision SafeSearch.

The `Moderator` protocol gains an `AzureContentSafetyModerator` implementation calling Content Safety's image analysis endpoint. The category mapping (Adult / Violence / Racy / Medical → quarantine) is rewritten for Content Safety's category set (Sexual / Violence / SelfHarm / Hate; each with a 0-6 severity score). Threshold: any category at severity ≥ 4 quarantines the photo.

Rejected: Custom Vision (requires training data we don't have), third-party (not in credit umbrella).

### 8. Observability: Azure Monitor + Application Insights + Log Analytics

Replaces Cloud Monitoring + structured Cloud Logging.

Container Apps streams stdout/stderr to a Log Analytics workspace; the FastAPI app emits structured JSON logs (`structlog` config unchanged); Azure Monitor alarms replace Cloud Monitoring alarms; the dogfood dashboard from Phase 11 is rebuilt as an Azure dashboard.

### 9. Frontend hosting: Azure Static Web Apps

Replaces Firebase Hosting.

Two Static Web Apps: `dragonfly-landing-swa` for the apex + www (corresponds to GCP `dragonfly-landing-dev`) and `dragonfly-parents-swa` for `parents.dragonfly-app.net`. Custom domain wiring is the same shape as on Firebase Hosting (one TXT for ownership, one CNAME or A); cert provisioning is automatic.

### 10. DNS: Azure DNS

Replaces Cloud DNS.

The `dragonfly-app.net` zone is recreated in Azure DNS as the final cutover step. NS records at the registrar are updated to point at Azure DNS nameservers. Until then, Cloud DNS remains authoritative and we update records there to point at Azure resources as each service comes online.

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
- Adds a second cloud tenant (Entra External Identities is its own tenant separate from the management tenant `briandragonflyapp.onmicrosoft.com`).
- The handoff-token mechanic gets reimagined; the QR shape from PR #69 stays, the contents change to a backend-signed JWT.
- Five risk docs (0001-0006) continue to apply; new risk 0007 will track the migration cutover.
- The GCP architecture work (5 weeks, ~$0 spent) is not wasted — it validated the shape of the system; Phase 6/7 are mechanical translations.

## Open questions

- Whether to keep Cloud DNS during the migration (probably yes, until Phase 9) or cut over earlier.
- Whether to keep Firebase Auth temporarily as a fallback for emergency rollback (probably no — it adds complexity and the beta is small).
- Whether the kid handoff token needs RFC 8693 token-exchange semantics or a simpler "exchange this opaque token for an Entra session" custom endpoint (leaning custom; full token-exchange is over-engineered for one flow).
