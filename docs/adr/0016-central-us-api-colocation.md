# ADR 0016: Co-locate the Observation API with PostgreSQL in Central US

- **Status:** Accepted
- **Date:** 2026-07-12
- **Decider:** Brian
- **Related:** ADR 0010, ADR 0015, risk 0003

## Context

The W1 promotion contract requires the exact deployed dispatcher registry to
remain below 300 ms p95. The East US Container App and Central US PostgreSQL
server originally crossed regions for every SQL statement and savepoint.
Revision 45 completed the exact 50-sample workload at 888.16 ms p95 even
though PostgreSQL CPU and I/O were well below saturation. Unknown observations
passed, but catalog paths could not meet the budget while retaining the
required transaction and per-handler savepoint contract.

East US and East US 2 PostgreSQL provisioning are restricted for this Azure
subscription. Moving or replicating the canonical database would also add a
write freeze, promotion, split-brain, cost, and rollback risk that a supervised
W1 pilot does not need.

## Decision

Run the public API in a parallel Central US Container Apps environment against
the existing Central US PostgreSQL server:

- primary API: `hinterland-api-central` in
  `hinterland-cae-central-dev`, Central US;
- primary API identity: `hinterland-api-central-mi`, with only ACR pull,
  photo-blob contributor, Key Vault secrets user, and moderation-queue
  sender/receiver roles;
- rollback API: `hinterland-api` in `hinterland-cae-dev`, East US;
- every scheduled job and consumer remains only in the East US environment;
- storage, ACR, Key Vault, Service Bus, Content Safety, and Log Analytics remain
  in East US; both environments write to the same Log Analytics workspace;
- PostgreSQL remains the one canonical writer in Central US.

Every deployment must run migrations before consumers and then pin the primary
API, rollback API, and every East US job to the same immutable image digest.
The Central revision is the canonical readiness, authenticated-canary, privacy,
and dispatcher-benchmark target. The East API stays deployable and healthy so
cached DNS and rollback never serve a schema-incompatible revision.

The public issuer, audience, and hostname stay unchanged. DNS cutover is
performed only after the Central generated hostname passes the complete W1
canary and exact 50-sample p95 gate. The East custom-domain path remains intact
until the Central certificate is independently valid and the Central endpoint
passes an SNI/TLS probe. Public rollback changes DNS back to East; it never
rolls back or forks the database.

## Evidence

The additive Central deployment used the same revision-45 immutable digest as
East and passed:

- health, readiness, kid JWKS, and trusted-parent CORS;
- BlockBlob upload, exactly-once replay, one Journal row, `pilot_private`,
  minimized child DTO, and signed-photo denial;
- 50 mixed Unknown/catalog/coarse observations at 62.12 ms p95 and 92.02 ms
  maximum on the exact Central revision.

This pre-cutover evidence proves the topology removes the network floor. It is
not the final protected promotion or Play Internal evidence.

## Consequences

- The API hot transaction path is co-located without moving stateful data.
- Photo/blob, Service Bus, Key Vault, and ACR calls remain cross-region, but
  they are not multiplied by dispatcher savepoints and are small at W1 scale.
- During DNS propagation, East and Central may both serve traffic. Shared
  PostgreSQL, idempotency records, and per-user locks make that safe only while
  both run the exact same digest and settings.
- Two API apps exist temporarily. Consumption scale-to-zero has no fixed
  environment charge; W1 bandwidth is negligible.
- Jobs are not cloned to Central, preventing duplicate schedules and consumers.
- Deleting the East API, certificate, environment, or identity requires a
  separate decision after the W1 evidence and rollback window.

## Rejected alternatives

- **Move PostgreSQL to East US:** subscription capacity is restricted and the
  stateful cutover has materially greater recovery risk.
- **Upgrade PostgreSQL only:** compute cannot remove the approximately 28 ms
  inter-region network floor.
- **Code-only SQL reductions:** useful later, but the required client-issued
  handler savepoints still leave catalog paths above 300 ms cross-region.
- **Clone jobs into Central:** risks duplicate scheduled work and queue
  consumption without helping the synchronous Observation transaction.
- **A separate dispatcher service:** adds service authentication, telemetry,
  and partial-failure semantics for a problem solved by stateless API placement.
