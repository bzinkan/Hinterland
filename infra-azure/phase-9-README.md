# Phase 9 — Observation async operations

ADR 0015 replaces the original BlobCreated/Event Grid and public-iNaturalist
pipeline. The old implementation is not a rollback option.

Use:

- [`phase-9-observation-w1.sh`](phase-9-observation-w1.sh) for contained W1
  provisioning, migration preflight, required jobs, lifecycle, and kill
  switches;
- [`phase-9-observation-monitoring.sh`](phase-9-observation-monitoring.sh) for
  Observation alerts; and
- [`phase-9-observation-README.md`](phase-9-observation-README.md) for exact run
  order and promotion gates.

`phase-9-async-pipeline.sh` remains only as a compatibility entry point and
delegates to the W1 provisioner. It cannot recreate direct Event Grid
moderation or iNaturalist workers.

The existing general [`phase-9-monitoring.sh`](phase-9-monitoring.sh) remains
for baseline API/PostgreSQL alerts; apply the Observation monitoring script in
addition and synthetically verify all receivers.
