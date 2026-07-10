# Risk 0003: Dispatcher production p95 still needs pilot proof

- **Status:** Open
- **Date filed:** 2026-05-10
- **Updated:** 2026-07-09 for ADR 0015
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
(16.04 ms minimum, 135.81 ms maximum), below the 300 ms code-level budget. This
does not replace evidence from the exact Azure/release-AAB environment.

## Remaining Closure Checklist

- [x] Implement low-data rarity fallback and duration logging.
- [x] Cover documented snapshots and real-PostgreSQL duplicate/replay cases.
- [x] Prove SQL savepoint rollback, blocked dependencies, persisted restore,
      Expedition replay gates, dispatcher/rebuild serialization, and
      replacement first-find rebuilds.
- [x] Run a 50-dispatch disposable PostgreSQL probe below 300 ms p95.
- [ ] Run pilot traffic until at least 50 deployed observations exist.
- [ ] Confirm deployed Log Analytics p95 below 300 ms.
- [x] Add Azure Monitor configuration for sustained p95 above 300 ms.
- [ ] Deploy and synthetically verify that alert in isolated Hinterland.

## Mitigation

Dispatcher failure does not fail Observation submission. The saved response is
`pending|partial`, mobile says rewards are catching up, and replay recovers
durable incomplete work. W1 promotion still requires exact-release p95 and
alert evidence.
