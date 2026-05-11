# Dragonfly Privacy Policy (DRAFT)

> **STATUS: DRAFT, NOT FOR PUBLICATION.** This file is a structural starting
> point, not legal copy. Before going live as `https://dragonfly-app.net/privacy`
> it must be reviewed by a lawyer with COPPA / kids-app experience. The
> sections below capture what Dragonfly actually does with data, in plain
> English. The lawyer's job is to translate that into the contractual /
> regulatory language that App Store + Play Store + COPPA require.

Last updated: 2026-05-10 (draft)

---

## What Dragonfly is

Dragonfly is a citizen-science field app for kids ages 9-12. Kids photograph
plants and animals they find outdoors. Each photo + the location it was taken
becomes a scientific observation that contributes to iNaturalist, a global
species database used by researchers.

A parent or teacher creates the kid's account. The kid never types an email
address, never sees ads, and never communicates with anyone outside their
family/classroom group.

## What we collect from a kid account

- **A photo of an organism (plant, animal, fungus, etc.).** Stored encrypted
  at rest in Google Cloud Storage. Photos that pass automated moderation are
  retained as part of the observation record. Photos that fail moderation
  (Cloud Vision SafeSearch flags adult / violence / racy / medical content) are
  moved to a teacher review queue and auto-deleted after 90 days if not
  approved (see [moderation.md](moderation.md)).
- **The location the photo was taken**, rounded to roughly city-block precision
  for caching purposes. Used to assemble the regional rarity context that makes
  the observation scientifically meaningful.
- **The species identification** (chosen by the kid, optionally suggested by
  iNaturalist's computer vision).
- **A timestamp.**
- **The kid's display name and group affiliation.** Display name is set by the
  parent/teacher; the kid never types their full name.

We do not collect:

- Email addresses for kid accounts (kids authenticate via Firebase custom
  tokens issued by their parent's app)
- Phone numbers
- Browser cookies, advertising IDs, or any tracking identifiers
- Voice recordings or microphone access
- Contacts or calendar
- Any data about other apps on the device

## What we collect from a parent/teacher account

- An email address (Firebase Authentication; used only for sign-in)
- A display name
- The groups they own/teach
- Standard service operational data (request logs, error reports)

## What we do with the data

- **Scientific contribution.** Observations (photo + location + species) are
  submitted to iNaturalist via Dragonfly's project account. Submissions become
  part of the public iNaturalist record under the project's name. Per
  iNaturalist's own privacy posture, location data may be obfuscated for
  threatened species. Kid display names are not transmitted to iNaturalist.
- **Moderation.** Every photo is screened by Cloud Vision SafeSearch before
  being shown to other group members or submitted to iNaturalist (see
  [`docs/moderation.md`](moderation.md) and [ADR 0009](adr/0009-moderation-provider-cloud-vision-safesearch.md)).
- **Reward and feedback.** The dispatcher computes "first find" / "rare in your
  region" / "expedition step complete" badges and shows them in the app. All
  reward computation happens server-side from data the app already has.
- **Operational logs.** Cloud Logging captures structured request logs for
  debugging. Logs are retained for 30 days. Photo bytes are never logged.

## What we do NOT do with the data

- We do not sell, rent, or share data with third-party advertisers
- We do not use kid data to train AI models (per ADR 0007: no kid-facing
  runtime LLM, no LLM training on kid input)
- We do not share data with anyone outside the kid's group except where
  iNaturalist submissions are inherent to the product purpose
- We do not send marketing emails or notifications
- We do not engage in profiling, ad targeting, or tracking-based personalization
- We do not transfer data outside the United States

## Children's Online Privacy Protection Act (COPPA)

Dragonfly is designed for users ages 9-12. We comply with COPPA via:

- **Verifiable parent consent.** A parent creates the kid account and consents
  to data collection at signup. The consent flow is plain English and lists
  every data category collected.
- **Data minimization.** We collect only what's necessary for the scientific
  observation and the celebration sequence. No analytics, no advertising IDs.
- **Parent access and deletion.** Parents can view, export, and delete their
  kid's data at any time via the parent app. Deletion is immediate and removes
  the data from our database; the iNaturalist contribution is requested for
  removal at the same time (subject to iNaturalist's own retention policy).
- **No third-party trackers.** Sentry (error reporting) is the only third-party
  SDK and is configured with `sendDefaultPii: false` (no IP, no user identifier).
- **Annual review.** This policy is reviewed annually by counsel.

## Account deletion

Both parents and teachers can delete their account (and all associated kid
accounts) from Settings → Delete account. Deletion is immediate at the
application level; the underlying database rows are purged within 24 hours and
all photos are removed from Cloud Storage within 7 days. iNaturalist
contributions submitted under the project account are requested for deletion
via iNaturalist's standard process.

## Contact

For privacy questions or to exercise the rights above, email
**privacy@dragonfly-app.net**. We respond within 7 days.

## Changes to this policy

Material changes will be communicated via the app's Settings tab AND via email
to the parent/teacher account on file. Continued use after a material change
constitutes acceptance.
