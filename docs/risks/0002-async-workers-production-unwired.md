# Risk 0002: Azure async safety/science pipeline not wired for closed beta

- **Status:** Scoped down via Option B (2026-06-04)
- **Date filed:** 2026-05-10
- **Updated:** 2026-06-04 for ADR 0010 + Option B
- **Owner:** Brian
- **Source:** Phase 8 exit criteria and ADR 0010 Azure migration

## 2026-06-04 — Option B decision

Outbound iNat submission was reframed under **Option B**: Dragonfly
does NOT post kid observations to iNat while the kid is under 13
(iNat ToS requires 13+). The iNat-submit pipeline ships dormant via
`Settings.inat_submit_enabled` defaulting to False (PR #6).

What this means for Risk 0002 closure:

- **Moderation half is required** for closed beta. Azure Content
  Safety must be provisioned + wired; the `moderation-pending`
  queue + Event Grid subscription + `dragonfly-moderation-worker`
  Container App must be running; flagged photos must route to the
  review queue. This stays a Risk 0002 close-on-beta item.
- **iNat-submit half is dormant by default.** The pipeline is built
  and tested (PR #109 producer + #110 consumer + #111 replay), but
  no `inat_submit_outbox` rows get written while
  `inat_submit_enabled=False`. The Service Bus `inat-submit` queue
  and `dragonfly-inat-submit-worker` Container App sit idle. The
  infra stays provisioned so flipping the flag back on is a
  zero-deploy operator action when iNat policy or product scope
  changes (e.g. a Phase 3 family-account model where the parent's
  iNat account posts on behalf of the kid).

Closing condition for Risk 0002 changes to **moderation-only**:
once Azure Content Safety is provisioned, the moderation worker
clean/flagged paths smoke-pass on dev, and DLQ alerts fire on
synthetic failures, the risk closes. The iNat-submit closure
criteria are descoped to a Phase 3 follow-up.

## What We Have

The code surface exists:

- `AzureContentSafetyModerator` behind the `Moderator` protocol.
- `process_pending_photo()` for pending -> clean/quarantine lifecycle.
- Adult review queue endpoints and mobile review UI.
- iNat submitter code with idempotency by Hinterland observation id.
- Admin jobs for rarity refresh, stale-review sweep, and dispatcher replay.

The W1 Android Internal Testing pilot may run with:

- `DRAGONFLY_MODERATION_PROVIDER=noop`
- no iNat OAuth token configured
- no public iNaturalist submission
- adult-supervised manual review of the one or few pilot observations

That W1 posture is intentional and does not close this risk.

## What Is Still Missing For Closed Beta

- Azure Blob/Event Grid or Service Bus trigger for `pending/` photo moderation.
- Azure internal caller auth model for `/internal/*` or direct worker execution.
- `DRAGONFLY_MODERATION_PROVIDER=azure_content_safety` configured from Key Vault.
- `observations.moderation_status` and `observations.moderation_labels` migration.
- Clean moderation path enqueues iNat submit work only after safety decision.
- iNat retry/DLQ/dead-letter visibility.
- Scheduled Azure jobs for rarity refresh, stale-review sweep, and dispatcher replay.
- Azure Monitor alerts/dashboards for API errors, moderation failures, iNat failures,
  queue/DLQ depth, dispatcher replay backlog, Postgres pressure, and budget.

## Closure Checklist

- [ ] Close Risk 0001: iNat project account/OAuth token and 50-photo benchmark.
- [ ] Choose Azure async primitive: Event Grid -> queue -> Container Apps job, or
      Service Bus queue directly from API/worker code.
- [ ] Implement internal auth for Azure callers or remove HTTP `/internal/*`
      exposure in favor of queue/job execution.
- [ ] Configure Azure AI Content Safety endpoint/key through Key Vault-backed
      Container App secrets.
- [ ] Add `observations.moderation_status` and `moderation_labels` migration and
      update the processor to write them.
- [ ] Wire clean moderation path to enqueue iNat submit.
- [ ] Wire retry/DLQ and alerting for iNat submit.
- [ ] Wire scheduled Azure jobs for rarity/sweep/replay.
- [ ] Verify clean path: real safe photo goes pending -> observations and remains
      reviewable by the family/group.
- [ ] Verify flagged path: known test image goes quarantine -> review queue and
      approve/reject works.
- [ ] Verify iNat path: clean approved observation appears in iNaturalist within
      the target window.

## Mitigation

The kid hot path is structurally safe while this risk is open: submission returns
after the observation write and dispatcher run. Moderation/iNat outages cannot
fail the kid's submit response.
