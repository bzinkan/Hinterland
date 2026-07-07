# ADR 0014: Firebase/GCP Decommission

- **Status:** Accepted
- **Date:** 2026-07-07
- **Deciders:** Solo author
- **Related:** ADR 0010 (Azure target architecture), ADR 0012 (Hinterland rebrand and Gordi Azure environment), ADR 0013 (Hinterland rename)

## Context

ADR 0010 moved the runtime from GCP to Azure but kept a small Firebase/GCP
footprint as a rollback and DNS/hosting convenience. That compromise is now
more confusing than useful: the active app, API, auth model, storage, and
deploy path are Azure, while stale Firebase references make agents and humans
reach for the wrong tools.

The current active domains and resources are:

- API: `https://api.thehinterlandguide.app`
- Landing/legal: `https://thehinterlandguide.app` and
  `https://www.thehinterlandguide.app`
- Parents web: `https://parents.thehinterlandguide.app`
- Azure resource group: `hinterland-dev-rg`
- Container App: `hinterland-api`
- ACR: `hinterlandacrdev`
- Static Web Apps: `hinterland-landing-swa`, `hinterland-parents-swa`

## Decision

Azure is the only active platform for Hinterland runtime, auth, hosting,
storage, CI/deploy, observability, and runbooks.

Remove the active Firebase/GCP paths from the repository:

- no Firebase client SDK in mobile
- no Firebase email/password native sign-in
- no Firebase Hosting deploy workflows
- no Firebase hosting config files
- no GCP Terraform plan workflow or GCP Workload Identity Federation deploy path
- no active runbook that instructs operators to deploy or smoke through
  Firebase/GCP

Adults authenticate through Microsoft Entra External Identities. Kids
authenticate through Hinterland-signed RS256 handoff/session JWTs. Native adult
setup remains web-first through the parents web app; native kid access remains
QR/dev-login.

Do not drop `users.firebase_uid` in this ADR. It is a legacy compatibility
column until a live-data audit and migration prove it is safe to remove. Code
may continue to carry tests and schema references that protect existing rows,
as long as they are explicit about being legacy compatibility.

Historical ADRs, old phase logs, and `infra-gcp/` may still mention GCP or
Firebase as history. They must not be used as active implementation guidance.

## External Decommission Gates

External deletion is intentionally gated and must not happen merely because
this ADR exists. Before deleting or deactivating external Firebase/GCP
resources, verify:

1. `https://api.thehinterlandguide.app/health` and `/ready` pass.
2. `https://thehinterlandguide.app`, `https://www.thehinterlandguide.app`, and
   `https://parents.thehinterlandguide.app` return 200 with valid TLS.
3. GitHub `main` CI, CodeQL, Azure API deploy, Azure landing SWA deploy, and
   Azure parents SWA deploy are green for the current main commit.
4. DNS records for apex, `www`, `parents`, and `api` are stable on Azure-backed
   targets.
5. A human confirms the retention plan for any old Cloud SQL, GCS bucket,
   Firebase Auth, and Cloud DNS data.

After those gates, the external cleanup order is:

1. Remove unused GitHub GCP/Firebase secrets.
2. Delete/deactivate Firebase Hosting sites `dragonfly-parents-dev` and
   `dragonfly-landing-dev`.
3. Disable Firebase Auth/project usage for `dragonflyapp-495423`.
4. Delete the Cloud DNS zone only after every record is recreated and verified
   in Azure DNS or the active registrar/DNS provider.
5. Delete the GCS bucket and old Cloud SQL instance only after explicit data
   retention confirmation.

## Consequences

- The repo has one active platform story. This reduces deploy mistakes and
  makes the phone/app debugging path shorter.
- Firebase SDK removal shrinks the mobile dependency graph and removes the old
  native adult password form.
- Rollback to the old GCP stack is no longer a supported operational path.
- The `DRAGONFLY_*` env prefix, bundle ids, and JWKS path remain compatibility
  names until separately migrated under ADR 0013/0012.
