# App Store Compliance Checklist

Re-verify official Apple and Google policy before every submission. This file is
the Hinterland-specific checklist, not legal advice.

Before entering public landing/support URLs in Google Play Console, run
[`landing-pre-play-checklist.md`](landing-pre-play-checklist.md) and the deploy
smokes in [`landing-deploy-runbook.md`](landing-deploy-runbook.md).

## Google Play Internal Testing First

The first store surface is Google Play Internal Testing for a tiny,
adult-supervised W1 pilot.

- [x] `play-internal` uses stable package `com.dragonfly.app`.
- [x] `play-internal` display name is `Hinterland Internal`.
- [x] `play-internal` blocks `ACCESS_FINE_LOCATION`.
- [x] `play-internal` explicitly requests `ACCESS_COARSE_LOCATION`.
- [x] Expeditions relevance uses already-granted coarse location passively
      (permission CHECK only, no new prompt; observe-submit stays the only
      requester). Only an on-device geohash4 cell (roughly 20 by 40 km) is sent. See
      risk 0007's 2026-07-02 update.
- [ ] AAB built with EAS `play-internal` profile.
- [ ] Generated manifest verified: no fine-location permission.
- [ ] Play Console internal tester list created outside the repo.
- [ ] Consent captured for each pilot family before kid provisioning.
- [ ] Physical Android pilot script passes before first kid test.

Internal Testing may defer Data Safety, Designed for Families opt-in, content
rating, and listing-permission copy. Those become blockers before Closed/Open/
Production tracks.

## Shared Closed/Public Release Blockers

- [ ] Lawyer-reviewed privacy policy live at `https://dragonfly-app.net/privacy`.
- [ ] Lawyer-reviewed Terms live at `https://dragonfly-app.net/terms`.
- [ ] `support@dragonfly-app.net` monitored.
- [ ] `privacy@dragonfly-app.net` monitored.
- [ ] In-app account deletion request verified from Settings.
- [ ] Operator process documented for full deletion of linked kids/photos/iNat
      contributions after `DELETE /v1/me`.
- [ ] Screenshots use seeded test data only.
- [ ] Third-party SDK audit complete. Phase 1 should have no ad SDKs.
- [ ] iNat public submission policy reviewed after Risk 0001 closes.

## Google Play Data Safety Draft Answers

- Photos: collected for app functionality.
- Approximate location: collected for app functionality and app
  personalization (observation pins; expedition suggestions are ranked from
  an on-device geohash4 cell, roughly 20 by 40 km — raw coordinates never leave the
  device for that feature).
- User content/species selection: collected for app functionality.
- Email address: adult accounts only, for account management/auth.
- Encrypted in transit: yes.
- Encrypted at rest: yes, Azure managed encryption.
- User can request deletion: yes, Settings -> Request account deletion, with
  operator follow-up for full erasure.
- Ads/tracking: no.

## Apple Draft Notes

- Category: Education primary.
- Age rating: likely 9+.
- Kids Category: target yes, after legal/SDK review.
- Privacy Nutrition Label should reflect photos, approximate location (app
  functionality + product personalization for expedition suggestions), and
  user content as app-functionality data, with lawyer review before
  submission.
- No IAP, ads, public chat, DMs, or kid-facing external links in Phase 1.

## Ongoing Risks

- Store location policy can change; verify current official docs before Closed
  Testing or production.
- Kids-app review can reject small copy/data-safety inconsistencies. Budget at
  least one rejection cycle.
- Privacy and Terms URLs must stay live with HTTP 200.
