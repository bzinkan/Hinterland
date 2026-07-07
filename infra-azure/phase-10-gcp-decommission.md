# Phase 10 -- GCP/Firebase decommission record

Date: 2026-06-03. Updated: 2026-07-07.

ADR 0014 supersedes the earlier "keep the cheap fallback" posture. The active
repo, CI, mobile auth path, landing/parents hosting, API runtime, and runbooks
are Azure-only.

## Active Azure Targets

- API: `hinterland-api` in `hinterland-dev-rg`
- ACR: `hinterlandacrdev`
- Public API: `https://api.thehinterlandguide.app`
- Landing/legal Static Web App: `hinterland-landing-swa` for
  `thehinterlandguide.app` and `www.thehinterlandguide.app`
- Parents Static Web App: `hinterland-parents-swa` for
  `parents.thehinterlandguide.app`
- App registrations: `hinterland-api` and `hinterland-client` in tenant
  `18dbd7fa-c411-49bc-82fc-9ccaa26e3404`

## Current Decision

Firebase Hosting/Auth/SDKs and GCP deploy workflows are removed from active
use. No active rollback plan should depend on Firebase Hosting, Firebase Auth,
Cloud Run, Cloud SQL, GCS, Cloud DNS, or GCP Workload Identity Federation.

The repository-side decommission includes:

- removing Firebase mobile SDK/config/sign-in code
- removing Firebase Hosting config files
- removing Firebase and GCP deploy workflows
- moving active smoke/deploy docs to `thehinterlandguide.app`
- recording current Azure app registrations and resources
- keeping `users.firebase_uid` only as a legacy compatibility field until a
  live-data audit proves it can be migrated/dropped safely

## External Deletion Gates

Do not delete external GCP/Firebase resources until these gates are met:

1. `https://api.thehinterlandguide.app/health` returns 200.
2. `https://api.thehinterlandguide.app/ready` returns 200.
3. `https://thehinterlandguide.app`,
   `https://www.thehinterlandguide.app`, and
   `https://parents.thehinterlandguide.app` return 200 with valid TLS.
4. GitHub `main` CI and Azure deploy workflows are green.
5. Data-retention confirmation exists for old Cloud SQL/GCS data.
6. DNS records needed by the current domains are present outside the old Cloud
   DNS zone.

After those gates, the explicit external cleanup list is:

- delete/deactivate Firebase Hosting sites `dragonfly-parents-dev` and
  `dragonfly-landing-dev`
- disable Firebase Auth/project usage for `dragonflyapp-495423`
- remove unused GitHub GCP/Firebase WIF secrets
- delete the old Cloud DNS zone only after all records are recreated and
  verified on the active DNS provider
- delete old Cloud SQL/GCS resources only after retention approval

## Historical June Outcome

The original Phase 10 migration stopped short of full deletion: Cloud Run was
removed, Cloud SQL was stopped, and Firebase Hosting/Auth plus Cloud DNS were
kept as a cheap fallback. That is no longer current guidance. Those resources
may still exist externally until the gates above are satisfied, but they are
not active product infrastructure.
