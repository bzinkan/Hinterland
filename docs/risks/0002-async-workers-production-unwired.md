# Risk 0002: Azure moderation is not yet cleared for closed beta

- **Status:** W1 contained; closed-beta gate open
- **Date filed:** 2026-05-10
- **Updated:** 2026-07-09 for ADR 0015
- **Owner:** Brian
- **Source:** Observation safety review, ADR 0010, ADR 0015

## Decision

Public iNaturalist submission is disabled for W1 and closed beta. Optional CV is
a separate post-clean disclosure/accuracy feature and remains disabled. A token
or dormant queue is not permission to enable either path.

Moderation is outbox-only. Observation finalization commits an attached
canonical photo and one `moderation_outbox` row. The relay is the sole Service
Bus producer. Direct Event Grid/BlobCreated moderation is forbidden and must not
be restored as rollback.

## Implemented Containment

- Azure SAS responses carry required upload headers including BlockBlob.
- Finalization validates/canonicalizes JPEG bytes before attachment.
- NoOp records `pilot_private`, never `clean`, and grants no signed URL.
- iNaturalist CV/submission have independent default-deny endpoint, producer,
  consumer, replay, and manual-endpoint boundaries.
- The moderation consumer rejects storage-event payloads and validates the
  committed observation/photo/object envelope.
- Provider egress repeats bounded decode and verified metadata/hash checks.
- Content Safety success fails closed without all four exact categories.
- Copy/move verifies destination length/hash before database commit and deletes
  source best effort afterward.
- Adult rejection tombstones and queues deterministic rebuild instead of
  decrementing counters piecemeal.
- W1 scripts discover Event Grid topics by storage-account source (not topic
  name), remove delivery, remove both old-API iNaturalist token aliases,
  remove/revoke public-iNaturalist work including inherited namespace roles,
  reconcile legacy pending photos before relay, pin jobs to one digest, and
  configure retention/health probes.

## W1 Posture

```text
MODERATION_PROVIDER=noop
INAT_CV_ENABLED=false
INAT_CV_DISCLOSURE_APPROVED=false
INAT_CV_BENCHMARK_APPROVED=false
INAT_SUBMIT_ENABLED=false
```

The outbox relay/consumer still runs so delivery is exercised, but every result
is `pilot_private`. Bytes are private, unsigned, and purged after seven days.
This supervised posture does not close the beta risk.

## Remaining Closed-Beta Environment Proof

- Provision/approve isolated Content Safety and Key Vault references.
- Run safe, flagged, unavailable, throttled, malformed-200, retry, lease-expiry,
  duplicate-delivery, destination-exists, DB-after-copy, and DLQ staging cases.
- Run approve/reject/stale winner and rebuild through deployed jobs.
- Verify retry exhaustion and failure alerting.
- Apply/verify 24-hour upload, seven-day pilot-private, and 90-day held-byte
  retention.
- Synthetically verify age, queue/DLQ, rebuild, conflict, mismatch, and job
  alerts.
- Run 24 hours or 25 submissions with zero duplicate observations,
  unauthorized reads, or stuck work.

## Closure Checklist

- [ ] Migrations precede deployment and API/all jobs report one digest.
- [ ] Event Grid moderation is absent; outbox relay is sole producer.
- [ ] Real Content Safety passes safe/flagged/unavailable/malformed probes.
- [ ] Review/rebuild canary has no duplicate counters or partial projections.
- [ ] Required alerts fire synthetically and resolve after repair.
- [ ] Canary duration/submission threshold completes cleanly.
- [ ] Architecture, data model, moderation, dispatcher, mobile, ingest, privacy,
      deployment, and recovery docs match the deployed revision.

## Mitigation While Open

Keep W1 flags/private retention. If provider, worker, or review is unhealthy,
pause relay/consumers and preserve committed outbox state. Never mark work clean
manually and never restore pre-clean photo egress.
