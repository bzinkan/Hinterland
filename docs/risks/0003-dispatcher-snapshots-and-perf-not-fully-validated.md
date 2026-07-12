# Risk 0003: Dispatcher production p95 still needs pilot proof

- **Status:** Open
- **Date filed:** 2026-05-10
- **Updated:** 2026-07-12 for ADR 0016
- **Owner:** Brian

## Current State

Correctness is closed in code and against disposable PostgreSQL 16. Coverage
includes durable versioned handler rows, one outer transaction with savepoints,
failed/blocked dependencies, persisted rewards, incomplete-only replay under a
user lock, deterministic rebuild, Expedition contribution gates, geohash-3
rarity fallback, and duration logging.

Twelve real-PostgreSQL integration cases cover concurrent finalization,
savepoint failure/replay, dependency blocking, Expedition contribution,
handler-version mismatch, review races, dispatcher/rebuild serialization, and
replacement first-find rebuilds.

The 2026-07-09 development probe ran 50 durable dispatches at 61.41 ms p95
(16.04 ms minimum, 135.81 ms maximum), but used no-op handlers against local
PostgreSQL and therefore did not represent the deployed registry.

The exact version-10 W1 device run on 2026-07-12 exposed the real blocker:
the two exact-revision dispatches were 715.81 ms and 664.72 ms. The API is in
East US while the sponsored PostgreSQL server is in Central US, so the durable
ledger's per-handler flushes multiplied cross-region round-trip time. The code
repair now buffers final ledger outcomes until one outer flush and removes
Expedition's redundant full-Dex scan. Disposable PostgreSQL verifies a mixed
Unknown/catalog/active-Expedition registry probe plus a bounded SQL statement
budget for the exact Unknown/no-location device path; exact Azure evidence is
still required before this risk can close.

The first exact-revision 50-sample Azure probe on revision
`hinterland-api--0000045` completed every dispatch but confirmed that the risk
remains open. Aggregate p95 was 888.16 ms. Unknown/no-location was within budget
at 227.99 ms p95, while catalog/no-location was 1193.86 ms and
catalog/coarse-location was 1181.40 ms. Handler p95 values were 142.57 ms for
Dex, 167.18 ms for Rarity, 360.16 ms for World, and 115.66 ms for Expedition.
The promotion verifier's initial ARM-scoped Azure CLI OIDC session could not
mint the separate Log Analytics data-plane audience. It now exchanges a fresh,
environment-scoped GitHub OIDC assertion for a short-lived data-plane token in
memory and queries the documented Log Analytics HTTPS endpoint directly. No
assertion or token is placed in the command line, logs, or promotion artifact.
That operational repair does not change or waive the 300 ms gate.

The measured handler timings track the approximately 28 ms East US API to
Central US PostgreSQL round trip, while PostgreSQL CPU and I/O remained well
below saturation. Even the practical remaining SQL reductions cannot preserve
the required client-issued savepoint around every handler and bring catalog
paths below 300 ms across regions. The next promotion therefore requires an
additive Central US API deployment against the existing canonical database,
with the East US API retained as rollback until the exact 50-sample probe and
public cutover both pass.

The additive Central US API then ran the same revision-45 immutable digest
against the canonical Central US database. Its generated hostname passed the
complete W1 upload/idempotency/Journal/private-photo canary. The exact Central
revision completed 50 mixed Unknown/catalog/coarse observations at 62.12 ms
p95 and 92.02 ms maximum. This proves the co-located topology satisfies the
budget before DNS, but risk closure still requires the protected workflow on
the final public revision and its alert evidence.

## Remaining Closure Checklist

- [x] Implement low-data rarity fallback and duration logging.
- [x] Cover documented snapshots and real-PostgreSQL duplicate/replay cases.
- [x] Prove SQL savepoint rollback, blocked dependencies, persisted restore,
      Expedition replay gates, dispatcher/rebuild serialization, and
      replacement first-find rebuilds.
- [x] Run a disposable PostgreSQL probe with mixed Unknown/catalog observations,
      real handlers, and an active Expedition below 300 ms p95; enforce the
      exact Unknown/no-location SQL query budget separately.
- [x] Prove the additive Central API with an exact-revision, 50-sample deployed
      Log Analytics probe below 300 ms before public DNS cutover.
- [ ] Run pilot traffic until at least 50 deployed observations exist.
- [ ] Confirm deployed Log Analytics p95 below 300 ms.
- [x] Add Azure Monitor configuration for sustained p95 above 300 ms.
- [ ] Deploy and synthetically verify that alert in isolated Hinterland.

## Mitigation

Dispatcher failure does not fail Observation submission. The saved response is
`pending|partial`, mobile says rewards are catching up, and replay recovers
durable incomplete work. W1 promotion still requires exact-release p95 and
alert evidence.
