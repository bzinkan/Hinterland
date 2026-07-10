# Risk 0005: Beta launch human/action items

- **Status:** Open
- **Date filed:** 2026-05-10
- **Updated:** 2026-07-09 for ADR 0015
- **Owner:** Brian

## Current State

Code is close enough for a controlled W1 Android Internal Testing pilot after
the pilot gates pass. Broader closed beta still needs legal, store, iNat, and
Azure operations work that an agent cannot finish alone.

## W1 Internal Testing Gates

- [ ] Additive migration succeeds before API cutover and API/all jobs use one
      immutable digest.
- [ ] Stale Cloud Run workflow is no-op and any accidentally recreated Cloud
      Run service is deleted.
- [ ] Public smoke probes pass.
- [ ] Authenticated Azure parent/kid smoke passes with an Entra parent token.
- [ ] Consent ledger writes and parent linkage are verified.
- [ ] iNat CV/submission gates are false and endpoint, producer, consumer,
      replay, manual, and stale-queue boundaries fail closed.
- [ ] Outbox-only NoOp records `pilot_private`; Event Grid moderation is absent
      and no non-clean state receives a signed URL.
- [ ] `play-internal` config blocks fine location and requests coarse only.
- [ ] Physical Android pilot passes durable offline/kill/retry, account
      isolation, catalog/manual/Unknown, optional coarse/no location,
      exactly-one rewards/counter, and private W1 lifecycle.

## Closed Beta Gates

- [ ] Privacy policy reviewed by a kids-app/COPPA lawyer and published.
- [ ] Terms of Service reviewed and published.
- [ ] `support@thehinterlandguide.app` and `privacy@thehinterlandguide.app`
      working.
- [ ] Account deletion follow-up process documented for linked kids/photos and
      any historical external contribution.
- [ ] Optional post-clean CV stays disabled unless Risk 0001 disclosure and
      benchmark gates close. Public iNaturalist submission stays disabled.
- [ ] Risk 0002 closed: Content Safety, outbox worker, review/rebuild,
      retention, canary, and alerts proven.
- [ ] Risk 0003 closed: exact Azure/release-AAB p95 and synthetic alert proof.
- [x] Risk 0004 closed: author-time draft expedition scaffold exists.
- [ ] Risk 0007 device verification complete.
- [ ] First beta family scheduled with a supervised onboarding slot.

## Close Criteria

This risk closes when the first beta family is invited, a real kid submits at
least one real outdoor observation through the Azure service, and the legal/
store/ops gates above are complete for the chosen release track.
