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
- [ ] Exact release AAB passes airplane-mode/kill-after-PUT/lost-response
      recovery with exactly one observation, counter update, and reward set.
- [ ] Account switching exposes no prior child's query, image, signed-photo,
      draft, or SQLite queue state.
- [ ] Location denial saves and PostgreSQL/log inspection finds no raw
      coordinates.

Internal Testing may defer Data Safety, Designed for Families opt-in, content
rating, and listing-permission copy. Those become blockers before Closed/Open/
Production tracks.

## Shared Closed/Public Release Blockers

- [ ] Lawyer-reviewed privacy policy live at `https://thehinterlandguide.app/privacy`.
- [ ] Lawyer-reviewed Terms live at `https://thehinterlandguide.app/terms`.
- [ ] `support@thehinterlandguide.app` monitored.
- [ ] `privacy@thehinterlandguide.app` monitored.
- [ ] In-app account deletion request verified from Settings.
- [ ] Operator process documented for full deletion of linked kids/photos/iNat
      contributions after `DELETE /v1/me`.
- [ ] Screenshots use seeded test data only.
- [ ] Third-party SDK audit complete. Phase 1 should have no ad SDKs.
- [ ] Azure Content Safety, review/rebuild, retention, and operational canary
      gates pass before Closed Testing.
- [ ] Optional post-clean iNat CV has separate disclosure and Risk 0001
      benchmark approval before enablement.
- [ ] Public iNaturalist submission remains disabled; future enablement needs a
      separate consent/geoprivacy ADR and store-disclosure review.

## Google Play Data Safety Draft Answers

- Photos: collected for app functionality.
- Optional approximate area (`geohash4` only): collected for app functionality
  and coarse Expedition relevance; raw coordinates are discarded before upload.
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
