# Mobile

Hinterland is a phone-first app. The web surface exists for parent-consent pages, the teacher dashboard, and admin tools — nothing a kid will ever see. This doc covers the mobile-specific decisions that the backend architecture doesn't cover: offline capture, permissions, build and distribution, and app-store compliance for a kids product.

Related reading: `architecture.md` (what the backend expects from the client), `onboarding.md` (the flows this platform has to serve), `moderation.md` (why the client uploads to `pending/` and not `observations/`), `expedition-authoring.md` (voice and tone that apply equally to in-app copy).

## Target platforms

**Primary: iOS and Android native**, shipped via Expo (React Native). One codebase, two app-store binaries, roughly 95% shared code with small per-platform shims for permissions copy and notification handling.

**Secondary: web**, same Expo codebase via Expo-for-web. Web is for the parent consent page, the teacher dashboard (review queue, class roster, welcome-sheet download), and the kid-account handoff QR page. Web is *not* the kid experience — no camera flow, no Dex browsing, no celebration sequence. If a kid opens the web app on a laptop in Phase 1, they see "Hinterland is best on a phone — here's a QR to get the app."

**Not supported: tablets as a first-class layout.** Tablets render the phone layout letterboxed. Classroom iPad use is a real Phase 1 concern but the phone layout is fine at iPad sizes; we don't build tablet-specific screens until post-beta usage data says we should.

## Why Expo and not bare React Native

Expo gives us, for free, the things a solo builder doesn't want to own: EAS Build (no local Xcode/Android Studio toolchain), EAS Update (OTA JS updates without app-store review), Expo Notifications (push without writing APNs/FCM plumbing), and a managed permission model that normalizes the iOS/Android differences. The cost is a ceiling on what native modules we can drop in without ejecting. For Phase 1 we have no need to eject: camera, location, SQLite, secure storage, notifications, filesystem, and image manipulation are all first-class in Expo.

If we hit a feature in Phase 3–4 that requires a custom native module (e.g. a specialized ML model running on-device), we'll reassess. We do not preemptively eject.

Expo SDK version is pinned in `package.json` and updated deliberately, not continuously. An SDK bump is its own PR with its own test cycle — kids' apps don't tolerate a day of breakage well.

## Durable Observation Queue

SQLite plus a normalized document-directory JPEG is the authoritative local
record until the server returns the first or replayed canonical observation.
Each row stores:

- one 26-character submission ULID and canonical owner user ID;
- local file path, decoded dimensions, byte count, and SHA-256;
- `observed_at`, optional `geohash4`, and location source;
- catalog taxon, display-only manual text, or Unknown identification;
- server photo/observation IDs when known; and
- stage, attempts, next retry, last safe error code, and request ID.

### Queue State Machine

1. `ready`: normalized JPEG and metadata are durable.
2. `presigned`: the API returned a photo ID, SAS, and upload headers.
3. `uploaded`: Azure PUT succeeded.
4. `complete`: first or replayed create returned the canonical observation.
5. `needs_attention`: non-retryable validation/idempotency conflict needs an
   adult or a new draft.
6. `abandoned`: explicit discard is awaiting local/server cleanup.

The same ULID is sent as `Idempotency-Key` for presign and create. Sync resumes
from the stored stage. Network errors, 408, 429, and 5xx retry with jittered
backoff. An expired SAS is refreshed through idempotent presign. A timeout or
process death after PUT resumes from `uploaded`; it never creates a second
submission.

The row and JPEG remain until canonical reconciliation. The queue is capped at
50 entries. Sync runs sequentially in the foreground/on reconnect; no client
reward, Dex, Expedition, or Sanctuary state is authoritative. A partial server
dispatch says rewards are catching up and reconciles through
`GET /v1/observations/{id}`.

### Account Isolation

Capture is authentication-gated and every queue/query/photo/image/draft key is
scoped by canonical user ID. On sign-out, token replacement, deletion, or kid
switch, in-flight requests are cancelled and server presentation caches are
cleared.

Unsynced rows remain owned by the original user. Sign-out warns the managing
adult and offers preserve-or-explicit-discard; the next account never sees or
processes another user's row or JPEG.

The parent who owns the group can restore that original kid on the device by
choosing **New sign-in QR** from the kid's Classroom roster row. The returned
15-minute handoff exists only in the modal's ephemeral state, is removed on
Done, expiry, group/account replacement, or unmount, and is never written to a
query cache, URL, log, or local storage. After exchange, foreground queue sync
continues only the rows whose owner matches the restored canonical kid ID.

### Immediate Identification

W1 offers bundled/project-catalog search, manual display text, and Unknown.
Catalog selection sends an integer taxon ID and can earn Dex/rewards. The
server supplies the canonical name. Manual text and Unknown always save but are
Dex-ineligible. Pre-save iNaturalist CV is disabled.

## Permissions

Every permission Hinterland asks for follows the same pattern: **pre-prompt in our own UI first, then trigger the native dialog, then handle denial gracefully.** The pre-prompt explains *why* we want the permission in the kid's language. The native dialog is the yes/no. Denial is not fatal — each permission has a degraded-mode path.

### Camera

- **When.** First observation attempt in the first expedition step. Not at app install.
- **Pre-prompt copy.** "Next, you'll take a photo of what you found. We need your camera to do that — is that okay?" [ Yes, ask me ] [ Not now ]
- **Denial path.** "No problem. You can upload a photo from your camera roll instead." Triggers `expo-image-picker` as a fallback, which uses a separate library permission.
- **iOS Info.plist.** `NSCameraUsageDescription = "Hinterland uses your camera to take photos of plants and animals you find."`
- **Android.** `android.permission.CAMERA` in `app.config.ts`.

### Location

- **When.** After photo confirmation, as an optional coarse-area choice.
- **Pre-prompt copy.** "Want to add an approximate area? It can help with local discoveries. You can also skip this."
- **Precision.** Request approximate/coarse foreground location only, compute a
  four-character geohash locally, then discard raw coordinates before SQLite,
  requests, logs, or analytics.
- **Denial path.** Save normally with `geohash4=null` and
  `location_source=none`. Disabled services cannot block an Observation. A
  manual area, if offered, is coarse rather than a precise pin.
- **Imported photos.** Never silently attach current device location to a
  library image. Ask separately and default to no location.
- **iOS Info.plist.** Explain optional approximate area; do not request
  background or precise location.

### Photo library

- **When.** Only if the kid denies camera access, then separately if they tap "upload a photo" from the observation flow.
- **Denial path.** The flow simply cannot proceed. The kid is shown "We need either the camera or your photo library to make an observation. You can change this in settings." with a deep link to the OS settings for the app.
- **iOS Info.plist.** `NSPhotoLibraryUsageDescription`. Use read-only variant (`PHAccessLevel.ReadWrite` is *not* what we want); we never write photos to the kid's library.

### Notifications

- **When.** After the first successful observation, after the celebration, after the Dex reveal. Never before.
- **Pre-prompt copy.** "Want us to let you know when your expedition is ready for the next step, or when your grown-up reviewed something? You can change your mind anytime."
- **Denial path.** No notifications. The app never nags; denial is respected indefinitely. The kid can opt in later from settings.
- **COPPA note.** We only send transactional notifications (expedition-ready, teacher-reviewed-your-photo, parent-approved-your-account). We do not send marketing, re-engagement, or promotional notifications to kid accounts, ever. See the notifications section below for the full policy.

### Microphone, contacts, calendar, etc.

Not requested. If a future feature needs one of these, it goes through an ADR first — asking a kid's app for contacts is a red flag, and we should have a written reason before the request exists in the codebase.

## Push notifications

Expo Notifications handles APNs and FCM uniformly. The expo-push-token is stored on the `USER#<id>/PROFILE` row (field `push_token`, cleared on logout).

**Kid-account notification types, all transactional:**

- `expedition_ready` — a scheduled expedition becomes active (daily expeditions unlock at 6am local time).
- `teacher_reviewed` — a flagged observation was approved or rejected by the teacher.
- `parent_approved` — the parent consent flow completed; the kid can now sign in.

**Adult-account notification types:**

- `review_queue_pending` — new item in the teacher review queue (throttled to once per 2 hours to avoid annoying teachers).
- `kid_first_find` — parent opt-in only; a kid in the family group logged a first find.
- `weekly_digest` — Sunday 6pm local time, if opted in.

**Never:**

- Marketing. No "come back to Hinterland!" re-engagement pushes.
- Ads, partner content, cross-promotion.
- Geographic pushes based on kid location ("a rare bird was spotted near you!"). Even though this would be cool for the product, it requires location-usage semantics we haven't earned and COPPA-reviewed.

**Implementation note.** Silent push is used for expedition refresh (pull new expedition content without waking the kid's screen). All user-visible notifications are scheduled server-side by the appropriate worker (e.g. `expedition_ready` is fired by a Cloud Scheduler cron that reads expedition-progress rows in Postgres and sends via Expo's push API).

## Performance targets

These are not aspirational; the app must meet them on a 3-year-old mid-range Android device with 4GB RAM.

- **Camera ready-to-shoot:** under 1 second from tapping "take photo" to live preview. `expo-camera` is preinitialized on the observation screen's mount, not on tap.
- **Photo capture + local save:** under 2 seconds total from shutter to "next step" being tappable.
- **Upload photo size:** max 1600px on the longest edge, JPEG quality 0.8.
  `expo-image-manipulator` resizes after capture. The original is discarded;
  the normalized queue copy remains until canonical reconciliation.
- **Dex scrolling:** 60fps with 500 entries. The current Field Journal Species segment uses `FlatList` as placeholder UI; switch it to `FlashList` (Shopify's virtualized list) before real Dex volume, because an engaged kid can reach 500 entries and `FlatList` drops frames at that size.
- **Celebration animation:** 2-second sequence max. Reanimated 3 + Lottie for the particle effects. No layout thrashing, no JS-thread animation, all transforms run on the UI thread.
- **App startup to role picker:** under 2 seconds cold start on our target device. Splash screen is `expo-splash-screen`, dismissed immediately after the initial navigation render — not on a timer.

**Haptics.** Light haptic on photo capture, medium on observation submitted, success haptic pattern on first-find celebration. `expo-haptics`. Respectful, not constant.

## EAS Build and distribution

Three build profiles in `eas.json`:

**`development`** — local dev client. Built once per SDK bump, installed on Brian's and testers' devices via EAS internal distribution. Points at `https://api.dev.hinterland.example`.

**`preview`** — beta build for internal testers (TestFlight on iOS, Google Play Internal Testing on Android). Built on every merge to `main`. Points at `https://api.staging.hinterland.example`. Uses EAS Update channel `preview` so JS-only changes land without a new binary.

**`production`** — the app-store release. Built manually from a tagged release. Points at `https://api.hinterland.example`. EAS Update channel `production`.

**EAS Update policy.**

OTA updates are for JS bug fixes and content-only changes. Native changes (new Expo SDK, new native module, new permission, new app icon) require a new binary and a new store review.

The W1 `play-internal` profile is deliberately frozen to its embedded bundle:
it has a real EAS channel and runtime identity, but automatic update checks are
`NEVER`, no `play-internal` update is published, and the app must not manually
fetch one. This preserves the exact-AAB device evidence. Enabling W1 OTA later
requires a separately reviewed promotion contract.

Every EAS Update publish must be paired with a server-side expedition content version check. If the app's bundled expedition schema version doesn't match the server's, the app prompts the user to update — not silently fails.

**Code signing.** iOS certs and provisioning profiles are managed by EAS (no Xcode keychain rituals for the solo builder). Android keystore is managed by EAS, backed up in 1Password, and never lives on a laptop.

## App-store compliance for a kids product

Both stores have stricter rules for apps aimed at under-13 users. We are deliberately in scope of both, because this is the audience.

**Apple App Store.**

- Category: Education, age rating 9+ (because of photo-sharing, even moderated).
- Kids Category submission: yes, opts into Apple's Kids Category. Required once we confirm:
  - No third-party analytics SDKs. (Confirmed — see `onboarding.md` COPPA section.)
  - No third-party ads. (Confirmed — no ads, period.)
  - No in-app purchases in kid-facing flows. (Phase 1: no IAP at all.)
  - No external links out of the app to a website without a parent gate. (Confirmed — the only external link a kid sees is the iNat species page, and that's deferred to Phase 2 with a parent-gate interstitial.)
- Privacy Nutrition Label: `Data Not Linked to You` = photos, location (we do link it to a user internally, but not to identity — this needs a lawyer's read before submission).
- Data Use: one entry, `App Functionality` only. No `Analytics`, no `Product Personalization` without a specific disclosure.

**Google Play.**

- Category: Education.
- Target age group: `Ages 9 & Up`. This opts into Google's "Teacher Approved" program eligibility (not a submission we need to make, but the compliance bar is the same).
- Designed for Families program: yes. Requires:
  - Ads SDKs from Google's approved list only. (N/A — no ads.)
  - No interest-based ads to users under 13. (N/A — no ads.)
  - Data Safety form: same disclosures as Apple's nutrition label.
- Permissions must be justified in the store listing. Each permission has a one-sentence reason in the Play Console.

**Shared compliance.**

- No third-party SDKs that send behavioral data to outside servers. Sentry for error tracking (approved — non-behavioral), Expo's services, AWS — that's the list.
- No chat, DMs, or user-to-user text at all in Phase 1. Observations are visible to the group (parent-reviewed or teacher-reviewed) but there are no comments, likes, or messages. This sidesteps the chat-moderation problem entirely for Phase 1.
- Password recovery and account deletion are first-class, one-tap from settings. Google Play's account-deletion requirement is Play Console-verified; App Store's is checked at review.

**Rejection recovery.** Expect 1–2 rejections from App Store review for first-time submissions of a kids app. The common ones: insufficient justification for camera permission, missing parent-gate on external links, privacy policy URL returning a 404. Keep the privacy policy URL live from day one.

## Development environment

- **Expo SDK version:** pinned in `package.json`; update via dedicated PR.
  Expo dependency validation intentionally excludes
  `@shopify/flash-list`: the Field Journal uses tested 2.3.x behavior and its
  React Native peer range includes SDK 54, while Expo's bundled recommendation
  remains 2.0.2. Remove the exception only with a dedicated list-performance
  compatibility run.
- **TypeScript:** strict mode. No `any` without a `// TODO:` comment explaining why.
- **State management:** Zustand for client state, TanStack Query for server state. No Redux — the complexity doesn't earn its place at this scale.
- **Navigation:** Expo Router (file-based). Routes under `app/` map to screens.
- **Styling:** Nativewind (Tailwind for React Native) plus a small set of design tokens in `theme.ts`. No per-screen StyleSheet objects.
- **Icons:** Lucide React Native. One icon family for consistency.
- **Fonts:** two — one for body (system), one for the celebration sequence (a rounded display face loaded via `expo-font`).

### `app.config.ts`

One config file, environment-switched via `APP_ENV` (dev/staging/prod). Pulls:

- API base URL
- Expo project ID
- Push notification project ID (same as Expo for Phase 1)
- Bundle identifier / Android package (different per env: `app.thehinterlandguide.dev`, `app.thehinterlandguide.staging`, prod)
- Expo Update channel

Secrets do not live in `app.config.ts`. Build-time secrets (e.g. Sentry DSN) are EAS Secrets; runtime secrets do not exist on the client.

### `eas.json`

Profiles as described above. `preview` and `production` both have `autoIncrement: true` for build numbers. `development` does not.

### Sentry

- DSN loaded from EAS Secret.
- `sendDefaultPii: false`.
- `ignoreErrors`: common RN noise (`Network request failed` at the network boundary, because we handle that in the offline queue and don't want Sentry to page on it).
- Source maps uploaded per EAS build.

### Testing

- **Unit:** Jest runs in CI and includes component `.test.tsx` files. Cover
  every queue stage, expired SAS, kill/relaunch, cancellation, owner switching,
  queue cap, no-location behavior, full-image `contain` preview, and sequential
  persisted rewards.
- **E2E:** Maestro, one smoke flow per release — role picker → sign in → take photo → submit → see celebration. Runs against a staging build on EAS. Not blocking PR merge, blocking release.
- **Device farm:** BrowserStack or AWS Device Farm for physical-device smoke testing before production release. Two devices — one low-end Android, one mid-range iPhone — cover the variance we care about.

## What this doc doesn't cover

- **Android-specific app-links / iOS universal links for the invite-code deep link.** Handled by Expo's linking config; details go in `onboarding.md` when the deep-link flow is finalized.
- **Accessibility.** VoiceOver / TalkBack support, dynamic type, reduced motion. Target is WCAG AA by the end of Phase 1. Detailed audit doc to come.
- **Internationalization.** English-only for Phase 1. i18n infrastructure uses `i18next` and should be set up on day one so we don't retrofit strings later; locales beyond `en` are post-beta.
- **In-app purchases and subscription.** Not in Phase 1. When they exist, Apple's StoreKit and Google Play Billing both have kid-specific rules (parent-gated purchase confirmation, no purchase-pressuring UI). Separate doc at that point.
- **Tablet-specific layouts.** See above — not Phase 1.
