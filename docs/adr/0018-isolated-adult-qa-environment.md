# ADR 0018: Isolated Adult QA environment and release lane

- **Status:** Accepted; provisioning requires a separate recorded cost approval
- **Date:** 2026-07-30
- **Decider:** Brian
- **Related:** ADR 0010, ADR 0015, ADR 0016, ADR 0017

## Context

W1 Internal Testing intentionally uses NoOp moderation and stores photos as
`pilot_private`. That evidence is tied to one immutable Play v12 binary and one
deployed revision. Enabling Azure AI Content Safety, post-clean iNaturalist CV,
shared multi-family Groups, or clean-photo viewing in the W1 environment would
invalidate the evidence and broaden the data recipients for supervised child
testing.

Adult testers need to exercise the broader product before those capabilities
are suitable for a child pilot. They will use synthetic child profiles and
adult-owned, non-sensitive photos. The environment still handles images and
authentication credentials, so a feature flag in W1 is not sufficient
separation.

## Decision

Create an `adult_qa` release lane in a dedicated Central US resource group,
`hinterland-qa-rg`. It has separate compute, identities, secrets, object
storage, queues, moderation provider, auth registrations, kid signing keys,
domains, and parent web application.

The QA lane may share only:

1. `hinterlandacrdev`, where QA identities receive pull-only access;
2. `hinterland-law-dev`, with `environment=adult-qa` and
   `release_lane=adult_qa` on every QA record and query; and
3. the `hinterland-postgres-dev` server, using a dedicated
   `hinterland_qa` database, `hinterland_qa_app` role, password, migrations,
   backups, and connection secret.

Sharing the PostgreSQL server is a cost decision, not permission to share a
database. A deployment that resolves the W1 database, role, Key Vault,
storage account, identity, Service Bus namespace, queue, or resource group
fails before migration.

### QA resources

| Concern | Adult QA contract |
|---|---|
| Resource group | `hinterland-qa-rg`, Central US |
| Container Apps | `hinterland-cae-qa`, `hinterland-api-qa`, dedicated jobs |
| Managed identities | `hinterland-api-qa-mi`, `hinterland-jobs-qa-mi` |
| Key Vault | `hinterland-kv-qa` |
| Blob Storage | `hinterlandphotosqa`, private `qa-photos` container |
| Service Bus | `hinterland-sb-qa`, QA-only moderation queue and DLQ |
| Content Safety | `hinterland-content-safety-qa` |
| Database | `hinterland_qa` / `hinterland_qa_app` on the shared server |
| Adult auth | Dedicated QA Entra API/client registrations and `api://<qa-api-id>/user.access` scope |
| Kid auth | QA-only issuer, audience, signing keys, and key ID |
| API | `https://qa-api.thehinterlandguide.app` |
| Parent web | `https://qa-parents.thehinterlandguide.app` |

Every Container App and job is assigned exactly one QA user-assigned managed
identity. Its runtime map pins `AZURE_CLIENT_ID` to that identity's exact
client ID so `DefaultAzureCredential` cannot select a W1 identity or an
ambiguous credential.

`GET /v1/meta` and every API response identify the `adult_qa` release lane.
The QA mobile client expects that lane and fails closed before authentication,
capture, queue processing, or submission when the lane is missing or
mismatched.

### Safety and external egress

- Azure AI Content Safety receives canonical QA images asynchronously.
- iNaturalist CV receives an adult-owned image only after it is clean or
  adult-approved, the adult requests suggestions, the current QA disclosure is
  accepted, and all enable/disclosure/benchmark gates pass.
- The CV token is held only in the QA Key Vault and is never available to a
  submission worker.
- Public iNaturalist submission remains disabled and no submission credential
  or enabled consumer is allowed in Adult QA.
- Catalog, manual identification, and Unknown remain available through every
  provider failure.

Public contribution requires a later ADR, per-adult OAuth, explicit canonical
parent review, and coarse-or-no-location controls. A moderation result never
implicitly authorizes publication.

### Deployment and approval

`infra-azure/adult-qa-preflight.ps1` is read-only. It validates the isolation
contract and emits a conservative cost preview. Any future provisioning script
must first invoke its `ProvisionReadiness` mode with the exact operator phrase
`APPROVE_HINTERLAND_ADULT_QA_COSTS` and retain the sanitized approval record.
This ADR and its infrastructure files do not themselves authorize billable
resource creation.

The protected `adult-qa-promotion` workflow:

1. runs repository and disposable-PostgreSQL checks;
2. verifies the QA resource, domain, identity assignment and exact
   `AZURE_CLIENT_ID`, database, queue, lifecycle, and alert contracts;
3. builds one immutable image digest;
4. pins only preflight and migration jobs to that digest;
5. runs preflight and additive migrations;
6. pins workers/jobs, then the API, only after migration succeeds;
7. deploys and verifies the QA parent web build; and
8. publishes sanitized evidence without tokens, SAS URLs, child text,
   coordinates, or images.

The protected environment requires adult approval. Promotion does not provision
missing infrastructure and cannot target W1 or `gordi-pilot-rg`.

CV qualification uses a separate protected workflow. It temporarily enables a
run-scoped lease bound to one manifest digest, one run ULID, 50 allowlisted
observation/photo-hash pairs, and an expiry no more than 24 hours away. It
requires 50 actual provider calls and archives only aggregate accuracy and
deployment attestations. An `always()` cleanup disables the lease, removes its
runtime variables and secret mappings, and destroys the private manifest copy.
Normal kid-facing photo-helper capability remains false throughout
qualification. A later promotion can enable CV only when its run ULID and
aggregate evidence digest match protected operator-reviewed values.
Promotion, qualification, and the scheduled expiry sweep share one
non-canceling repository concurrency group, so only one may mutate the QA API
environment or secrets at a time.

## Consequences

- W1 v12 and its NoOp evidence remain immutable.
- Brian and Julia can install the QA app beside W1 and test broader behavior.
- QA has additional cost and operational surface, but its stop plan can disable
  the QA API, workers, CV, and moderation relay without touching W1.
- Shared ACR, telemetry, and PostgreSQL require explicit tags, database
  qualification, and allowlist checks on every promotion.
- Adult QA photos are test data, not disposable public data; private storage,
  retention, revocation, and deletion rules still apply.

## Rejected alternatives

- **Enable providers in W1:** rejected because it changes the safety and privacy
  posture of an evidenced child build.
- **Use a second schema in the W1 database:** rejected because a connection
  error could expose or mutate W1 data.
- **Use one shared iNaturalist token for CV and publication:** rejected because
  possessing a CV credential must not create a publication path.
- **Automatically publish clean observations:** rejected because moderation is
  not consent, identity authorization, or a geoprivacy decision.
