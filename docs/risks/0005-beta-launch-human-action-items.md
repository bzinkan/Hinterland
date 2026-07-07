# Risk 0005: Beta launch human/action items

- **Status:** Open
- **Date filed:** 2026-05-10
- **Updated:** 2026-06-04 for ADR 0010
- **Owner:** Brian

## Current State

Code is close enough for a controlled W1 Android Internal Testing pilot after
the pilot gates pass. Broader closed beta still needs legal, store, iNat, and
Azure operations work that an agent cannot finish alone.

## W1 Internal Testing Gates

- [ ] Azure deploy workflow is green or manually verified.
- [ ] Stale Cloud Run workflow is no-op and any accidentally recreated Cloud
      Run service is deleted.
- [ ] Public smoke probes pass.
- [ ] Authenticated Azure parent/kid smoke passes with an Entra parent token.
- [ ] Consent ledger writes and parent linkage are verified.
- [ ] iNat OAuth token is not configured.
- [ ] `play-internal` config blocks fine location and requests coarse only.
- [ ] Physical Android pilot script passes: parent web setup, kid QR scan,
      one observation submit, rewards/Sanctuary behavior, review queue.

## Closed Beta Gates

- [ ] Privacy policy reviewed by a kids-app/COPPA lawyer and published.
- [ ] Terms of Service reviewed and published.
- [ ] `support@thehinterlandguide.app` and `privacy@thehinterlandguide.app`
      working.
- [ ] Account deletion follow-up process documented for linked kids/photos/iNat.
- [ ] Risk 0001 closed: iNat project account/OAuth + 50-photo benchmark.
- [ ] Risk 0002 closed: Azure async moderation/iNat/jobs/alerts wired.
- [ ] Risk 0003 closed: real-DB dispatcher/replay proof and p95 measurement.
- [x] Risk 0004 closed: author-time draft expedition scaffold exists.
- [ ] Risk 0007 device verification complete.
- [ ] First beta family scheduled with a supervised onboarding slot.

## Close Criteria

This risk closes when the first beta family is invited, a real kid submits at
least one real outdoor observation through the Azure service, and the legal/
store/ops gates above are complete for the chosen release track.
