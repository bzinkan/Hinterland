# App Store Compliance Checklist

Pre-submission checklist for both the Apple App Store and Google Play. Sourced
from [`docs/mobile.md`](mobile.md) "App-store compliance for a kids product"
plus current store policy as of 2026-05-10. Re-verify the most recent policy
docs before each release submission since both stores tighten kids-app rules
roughly twice a year.

## Google Play first / Internal testing

The first store surface Dragonfly hits is the Google Play **Internal
testing** track for a 1-week controlled adult-supervised kid pilot.
See [`google-play-internal-testing.md`](google-play-internal-testing.md)
for the Play Console process and
[`one-week-kid-pilot-checklist.md`](one-week-kid-pilot-checklist.md)
for the operator's day-by-day checklist.

What Internal testing actually requires (much narrower than the rest
of this doc):

- [x] AAB built from a stable package name (`com.dragonfly.app`). The
  `play-internal` EAS profile produces this.
- [ ] Play Console app entry created. First uploaded artifact LOCKS
  the package name -- see the warnings in
  [`google-play-internal-testing.md`](google-play-internal-testing.md).
- [ ] Internal testing tester email list created with up to 100
  testers (kept in a private spreadsheet, NOT in the repo).
- [ ] Risk 0007 (precise-location vs Play Families policy) has a
  documented decision before the AAB is built. See
  [`risks/0007-google-play-families-location-policy.md`](risks/0007-google-play-families-location-policy.md).
- [ ] Parental consent captured via the `/consent` endpoint for every
  pilot family BEFORE the kid account is provisioned.

What can wait while Dragonfly is **exclusively on Internal testing**:

- Data Safety form (Play Console accepts Internal testing builds
  without it; required before Closed / Open / Production).
- Designed-for-Families program opt-in.
- Content rating questionnaire.
- Target audience confirmation.
- Permissions justification copy in the listing.

These ALL become production blockers the moment Dragonfly tries to
move to Closed testing or beyond. The full list of production
blockers is the rest of this document.

**Hard rule:** while this section is open (i.e. while we're still
running Internal testing only), do not promote a build past Internal
testing without first re-reading every item below and the active risk
docs.

## Pre-submission, shared

- [ ] Privacy policy published at `https://dragonfly-app.net/privacy`. The
  [DRAFT](privacy-policy-DRAFT.md) in this repo is a starting point only --
  the published URL must be lawyer-reviewed. **Both stores will reject without
  this.**
- [ ] Terms of Service published at `https://dragonfly-app.net/terms`. Even
  shorter than the privacy policy; covers acceptable use and the kid-facing
  product contract.
- [ ] Support email is live and monitored: `support@dragonfly-app.net`. Stores
  ping it before they ping us.
- [ ] Account deletion: in-app one-tap path verified end-to-end on both
  platforms. Google Play's Data Safety form will ask for the exact in-app
  navigation path.
- [ ] All third-party SDKs documented (Phase 1: Sentry only). Verify Sentry's
  current data-collection policies match what we configured
  (`sendDefaultPii: false`, no IP, no breadcrumbs containing PII).
- [ ] Screenshots show only seeded test content (no real kid photos, no real
  group names, no identifiable locations).
- [ ] App-store listing copy reviewed for:
  - No medical claims
  - No "best for ages X" superlatives that vary by jurisdiction
  - No marketing-style "fun!" exclamations that feel out of line with the
    respect-the-reader voice we use in product

## Apple App Store

- [ ] **Category:** Education (primary), Reference (secondary)
- [ ] **Age rating:** 9+ (photo sharing is a 9+ trigger even when moderated)
- [ ] **Kids Category:** Yes, opt in. Verify:
  - [ ] No third-party analytics SDKs (verified above)
  - [ ] No third-party ads, no IAP in kid-facing flow
  - [ ] Parent gate before any link out of the app (Phase 2 iNat species link
    needs an interstitial)
- [ ] **Privacy Nutrition Label:**
  - Data Used to Track You: None
  - Data Linked to You: None (the user_id <-> Firebase UID mapping is internal,
    not surfaced)
  - Data Not Linked to You: Photos, Location (precise), User Content (species
    selection)
  - Confirm with lawyer that "Data Not Linked to You" is accurate -- the
    internal link to a user is real, but Apple's definition is about external
    identification. Lawyer judgment.
- [ ] **App Privacy Report:** Make sure the live runtime matches the
  declarations. Apple cross-checks at review time.
- [ ] **In-App Purchase:** None in Phase 1. Confirm the entitlements file does
  not advertise IAP availability.
- [ ] **External links:** None reachable by kids in Phase 1. (iNat species page
  is post-Phase-1 with parent gate.)
- [ ] **TestFlight beta:** internal testing group invited only after
  privacy + ToS URLs return 200.

## Google Play

- [ ] **Category:** Education
- [ ] **Target age group:** Ages 9 & Up. Opts into "Teacher Approved" review.
- [ ] **Designed for Families program:** Yes. Confirm:
  - [ ] No ad SDKs at all (we have none)
  - [ ] No interest-based advertising
- [ ] **Data Safety form:** mirror the Apple Privacy Nutrition Label answers.
  Google asks more granular questions on collection purpose. Concrete answers
  for our case:
  - Photos collected: App Functionality (not Analytics, not Advertising)
  - Approximate location: App Functionality
  - User content (species selection): App Functionality
  - Encrypted in transit: Yes (HTTPS / TLS on Cloud Run)
  - Encrypted at rest: Yes (default GCS / Cloud SQL encryption)
  - User can request deletion: Yes (one-tap from Settings)
- [ ] **Permissions justification:** every requested permission has a one-line
  reason in the Play Console listing:
  - Camera: "Take photos of plants and animals for scientific observation"
  - Location: "Remember where each observation was made"
  - Photos: "Pick a photo from your library if you didn't take it with the
    camera"
  - (No microphone, contacts, calendar, SMS, phone -- per docs/mobile.md)
- [ ] **Internal Testing track:** signed AAB uploaded; reviewer accounts added
  to the testers list.
- [ ] **Closed Testing track:** beta group invited only after Data Safety form
  is submitted + approved.

## Both stores: ongoing

After launch, the kids-app review bars stay tight:

- [ ] Privacy policy URL must keep returning 200 in production. Apple has
  pulled apps within hours of a stale URL.
- [ ] Apple rejects roughly half of first kids-app submissions on minor copy
  issues. Plan for at least one rejection round.
- [ ] Google Play "Family Library" eligibility check runs annually; missing
  the response window is auto-rejection.
- [ ] Both stores require a yearly "Data Safety / Privacy Label" reaffirmation
  via the developer console. Calendar reminder needed.

## Local Dragonfly-specific risks

- [ ] **Apple may reject the kids-category submission for the location
  permission alone** if the privacy label isn't clear that location is per-
  observation and not background. Mitigation: the `NSLocationWhenInUseUsageDescription`
  string in `docs/mobile.md` explicitly says "We only use it for this
  observation."
- [ ] **Google Play "Designed for Families" requires a third-party SDK audit.**
  Sentry is on Google's approved-list for child-safe data handling, but
  verify the current version is still on the list at submission time.
- [ ] **Apple's "Kids Category" review historically takes 5-10 business days**
  (vs 1-2 for non-kids apps). Plan launch dates accordingly.
