# Onboarding and Registration

This doc covers every path a new user takes from "first time opening the app" to "first successful observation." Three personas, three different paths, and a COPPA consent step that gates every kid account.

The mechanism (Firebase Auth, ID token with `role` and `group_id` custom claims, Admin-SDK user creation for kids, 6-character join codes) is already specified in `architecture.md`. This doc is about the flows — the screens, the copy, the consent plumbing, and the five-minutes-after-install experience that decides whether a kid ever comes back.

Related reading: `architecture.md` (auth model, role attributes), `data-model.md` (User / Group / Membership rows), `mobile.md` (platform-specific permission and offline behavior the flows depend on), `expedition-authoring.md` (voice and tone rules that also apply to onboarding copy).

## The product position

Hinterland is invite-only during Phase 1 closed beta. Every account is created either by a parent, a teacher, or automatically through a parent/teacher. Kids never self-register — not because they can't, but because COPPA requires verifiable parental consent and because the "kid self-service signup" flows of other apps are exactly the sloppy pattern we're trying to avoid in an app for 9–12-year-olds.

Two things stay true across every flow:

**Adults do the complicated stuff.** Kids never see email forms, password rules, verification codes, or terms-of-service checkboxes. Adults set up accounts; kids enter a simple username and PIN.

**First observation before first settings screen.** We don't ask for data we haven't earned. The first time a kid opens the app, they should be outside and logging an observation inside 90 seconds. Profile photos, display name customization, and notification preferences are surfaced *after* the first celebration, not before.

## The three signup paths

### Parent self-signup

The primary Phase 1 path. A parent has received an invite code from the closed beta and wants to set up accounts for their own kids.

1. **Open app.** Role picker: "I'm a parent / I'm a teacher / I have a login." Copy is neutral — no "kid" button, because kids don't self-identify as kids in app UI.
2. **Tap "I'm a parent."**
3. **Invite code.** Single text field, prefilled from deep link if the invite came as a URL. Code format is `DRAGON-XXXX` (4 base32 chars after the dash; ~1M code space; redeemable once). Error copy on invalid: "That code doesn't match any invite. Check the email you got, or reply to it if you think this is a mistake."
4. **Sign up.** Email + password + display name. Password rules per Firebase Auth defaults (6+ chars; we tighten to 8+ chars + at least one digit at the client). Email verification — Firebase sends a verification link, parent clicks it. This is Firebase's native flow; we don't reimplement.
5. **COPPA acknowledgment screen.** One screen, short copy: "Hinterland is designed for kids 9–12. You'll create accounts for your kids here, and we'll ask you to approve what information they share. You can delete their account at any time." Accept button advances.
6. **Create family group.** Name defaults to "[Parent display name]'s family" (editable). Creating the group generates a 6-char join code — not shown to the parent because family groups don't use the code (see below); it exists for consistency with the data model.
7. **Add first kid.** Name (first name or nickname, 1–20 chars), age band (9–10, 11–12, 13+). Age band, not date of birth — we don't need DOB and COPPA data-minimization argues against storing it.
8. **Account generated.** App shows: "Your kid's login. Write this down or save it somewhere safe." Username is auto-generated, memorable, and not PII: `sparrow-22`, `pinecone-14`, `river-07`. PIN is 4 digits, displayed once, never shown again without parent re-auth.
9. **"Set up their device" screen.** Two options: "They'll use this phone" (stays on current device, switches session to the kid) and "They'll use their own device" (shows a QR code that the kid scans to auto-fill username + PIN on their device).
10. **Done.** Parent lands on the family dashboard. Either the kid takes the phone and starts their flow, or the parent switches back to add another kid.

**Done-with-onboarding** for a parent is: at least one kid account created. Adding more kids is one tap from the family dashboard and reuses steps 7–9.

### Teacher self-signup

The other Phase 1 adult path. A teacher has an invite code and wants to set up their class.

1. **Role picker → "I'm a teacher."**
2. **Invite code.** Same format as parent invites, different code pool (so we can track teacher vs parent adoption).
3. **Sign up.** Email + password + display name + school name (free text, not verified in Phase 1 — teacher-claimed). School name is stored on the teacher's user row for future grouping and optional verification.
4. **School-context acknowledgment screen.** Similar to the parent COPPA screen, different language: "You're creating a class for your students. Parents will receive an email asking them to approve their child's account. You can remove any student from your class at any time."
5. **Create class.** Class name, grade level (3rd–6th — matches our 9–12 target band), estimated student count (used to size the welcome sheet).
6. **Welcome sheet generated.** A PDF is generated on the backend and downloadable from the teacher dashboard. The PDF has one row per student: username, PIN, and a URL + QR code that goes to the parental consent page. The teacher prints the sheet, cuts it into rows, and sends one home with each student. Alternative: the teacher emails parent email addresses to a bulk-invite endpoint and we deliver the rows directly via email.
7. **Done.** Teacher lands on the class dashboard. It's empty until students' parents approve their accounts and the kids log in.

**Done-with-onboarding** for a teacher is: class created and welcome sheet downloaded. Students enrolling is out of the teacher's direct control (parents gate it) and not on the onboarding critical path.

### Kid via join code

Two sub-flavors, depending on who set up the account.

**Parent-created kid.** Account already exists. Kid just signs in.

1. **Role picker → "I have a login."**
2. **Enter username + PIN.** Keyboard is the kid-friendly variant (larger hit targets, numeric PIN pad). On success, the API mints a Firebase custom token (Admin SDK `createCustomToken`) for the kid's Firebase user; the client exchanges that custom token for a Firebase ID token via the Firebase Auth client SDK. The kid's `role` and `group_id` are set as custom claims on the Firebase user when the account is created.
3. **Welcome sequence.** One screen: "Hey [nickname]! Let's log your first find. You don't need to know what it is — the app will help." Dismissible but defaults to continuing into the first expedition.
4. **Auto-launch `backyard_starter`.** The first expedition IS the tutorial. No separate tutorial screens, no "here's how the app works" walkthrough. The first expedition's `intro` copy plus the step-by-step guidance during the first observation is the tutorial.
5. **Take first photo.** Pre-prompt before the camera permission dialog (see `mobile.md`). Photo, location confirm, taxon pick (iNat CV suggests, kid confirms).
6. **First submission.** Dispatcher runs, first celebration fires. `first_find` reward, `expedition_step` reward. The kid sees a species they logged and the Dex gaining its first entry.
7. **Reveal the map.** After the celebration, the app reveals the Dex and the expedition map. These were hidden before the first observation to avoid "empty state" fatigue.

**Done-with-onboarding** for a kid is: first observation submitted and celebrated. Sign-in alone doesn't count — a kid who signs in and bounces hasn't experienced the product.

**Teacher-created kid (with parental consent gate).** Account exists in Firebase Auth but is marked `pending_parental_consent=true` — a custom claim that prevents the ID token from being usable against any API endpoint. The kid's first sign-in fails with a friendly error until the parent has approved.

1. Teacher distributes welcome sheet.
2. Parent receives row with child's name, username, PIN, and a consent URL.
3. Parent opens the consent URL (in a browser — this is one of the few web surfaces, see `mobile.md`). Page explains what data Hinterland collects from their child and asks for approval. No account required from the parent in this minimal flow — parental consent is a one-time click-through plus a follow-up confirmation email 24 hours later ("email plus" method, COPPA-compliant for low-risk data collection).
4. On consent, the backend flips `pending_parental_consent=false` and records the consent event with a timestamp, the parent's email (from the URL's one-time token), and the consent version. 
5. Kid can now sign in normally. Flow is identical to the parent-created kid flow from that point.

Until consent is given, the kid sees a "Waiting for your grown-up to say yes" screen on sign-in. No data about the kid has been written to Postgres beyond the empty `users` row — we don't fetch their iNat CV results, don't write `dex_entries`, don't do anything. The account is a shell until consent lands.

## COPPA and data collection

Hinterland collects the minimum necessary to operate the app for kids under 13. Nothing more, nothing "just in case," nothing for analytics.

**From kids:** first name or nickname (1–20 chars, kid's choice or parent's choice), age band (9–10, 11–12, 13+ — not exact date of birth), observation photos, observation location (lat/lng rounded to 4 decimal places, ~11m precision), observation taxon ID (via iNat CV, kid-confirmed). That's it. No real name unless the kid chose to use it. No email. No profile photo in Phase 1. No friend lists beyond the class/family group they were placed in.

**From parents:** email, password, display name, optional school name if they're also the signup. Standard adult account data.

**From teachers:** email, password, display name, school name. Same shape as parents.

**Parental consent is one of two forms:**

1. **Parent-created kid:** consent is implicit in the parent creating the account under their own logged-in session. The API enforces that only an authenticated parent (Firebase ID token with `role=parent` custom claim) can call the kid-create endpoint; the kid's account is linked to the parent's via a `parent_user_id` field on the `users` row. The parent's creation action is the consent event.

2. **Teacher-created kid:** consent is explicit via the "email plus" method. Email 1 delivers the consent URL; parent clicks and approves; email 2 arrives 24 hours later confirming the consent and reminding the parent they can revoke at any time. Both emails are logged with timestamps in the `USER#<kidId>/CONSENT` row (see `data-model.md` — this entity needs to be added in the same PR as this doc lands).

**Audit-of-record (Phase 1).** Every consent click hits `POST /v1/auth/consent`, which writes a row to the `parent_consent_records` Postgres table and returns its ULID `id`. The columns capture exactly what the audit trail needs and nothing more: `parent_email`, optional `kid_display_name`, `policy_version` (e.g. `2026-05-10-DRAFT`, bumped any time the policy text changes materially), `recorded_at` (server time, tz-aware), `source` (currently always `web_consent`), and nullable `linked_parent_user_id` / `linked_kid_user_id` foreign keys to `users.id` filled in by the parent-signup flow once an Entra-verified token arrives. We deliberately do NOT store raw IP or User-Agent in Phase 1 — the `ip_hash` / `user_agent_hash` columns exist for a future operator-managed-salt scheme but stay NULL today. Structured logging continues to emit `auth.consent.recorded` carrying the same row id so existing log-based ops dashboards keep working; the row, not the log, is the long-term source of truth.

**Revocation is one click.** Parent dashboard has a "Delete my kid's account" button that soft-deletes the `users` row, cascades a hard-delete job for all `observations`, `dex_entries`, and `expedition_progress` rows under that user, deletes Cloud Storage photos, and tombstones the Firebase user via the Admin SDK. Executed asynchronously via a Cloud Tasks worker (Phase 1 Week 12); the parent gets an email confirming completion.

**No advertising. No third-party analytics SDKs.** The API service emits Cloud Logging structured logs and that's the entire analytics stack for kid-facing features. ADR 0002's "LLMs are author-time only" rule is the companion principle: nothing runs client-side on a kid's phone that sends their behavior to a third party.

## The first five minutes

For a kid, "first five minutes" determines whether they come back tomorrow. The entire onboarding is designed around the assumption that the kid is physically holding a phone and physically outside (or standing by a window). If they're in a car or in a waiting room, the experience should still work — `anywhere_starter` is designed for exactly that case — but the default assumption is active use.

The target sequence, start to finish, under 5 minutes:

0:00–0:30 — Role picker, tap "I have a login," enter username + PIN, see welcome screen.
0:30–1:00 — `backyard_starter` intro copy, "find a plant" step shown.
1:00–3:00 — Camera permission pre-prompt + native dialog, photo taken, location confirmed, taxon suggested and picked.
3:00–3:30 — Submission, dispatcher runs, celebration fires.
3:30–5:00 — Kid reads the celebration, maybe reads the expedition's outro copy, sees their Dex with one entry.

Cross-references:
- Camera permission pre-prompt copy: `mobile.md`.
- Expedition intro/outro voice: `expedition-authoring.md`.
- Celebration sequencing (reward weight ordering): `dispatcher.md`.

Things that *don't* happen in the first five minutes: email verification (parent did it already), profile customization, notification permission prompt (deferred to after first observation — asking for notifications before the kid has anything to be notified about is the classic mistake), friend/classmate browse, settings of any kind.

## Recovery and multi-device

**Forgotten kid PIN.** Parent's family dashboard has "Reset [kid's] PIN" — generates a new 4-digit PIN and displays it once. Same flow teachers use for kids in their class, with the added requirement that the parent receives an email notification of the reset.

**Forgotten parent/teacher password.** Firebase Auth's native password reset flow — email a reset link, click through, set a new password. No custom handling.

**New device.** Kid logs in on any device with username + PIN. Previous session on the old device is invalidated — only one active session per kid at a time. This is deliberately stricter than the parent/teacher model, because kid accounts are often shared between a family's devices and a classroom's iPads, and we want the "whoever signs in most recently has the session" rule to be predictable.

**QR sign-in for known devices.** On trusted devices (biometrics enrolled), the app can store the PIN in the Secure Enclave / Android Keystore after first successful sign-in, with parent approval. Next sign-in requires only the username and a biometric tap. Phase 2 polish, not Phase 1.

**Account handoff at age 13.** A kid who turns 13 can claim their own iNat account and transition to an adult-style account. This is the Phase 3 "claim flow." During Phase 1 we don't need to implement it, but we do need to not actively prevent it — which means age band is mutable (a kid's `9–10` can move to `11–12` and eventually `13+` via the parent dashboard) and we avoid any decision that assumes age is permanent.

## Design principles

**One question per screen.** The kid's signup is literally one screen for username + PIN. The parent's is broken into small screens for email, password, display name, each separately, because on a phone keyboard a single long form is hostile. This is the opposite of the web-era "all fields on one page" pattern.

**Defer everything deferrable.** Profile photo, friend list, notification preferences, privacy settings — none of these are in the onboarding flow. They exist in settings, reachable from the dashboard after the first observation.

**Never ask a kid for PII.** No last name, no school name, no address, no phone number, no email. The adult account holder owns the real identity; the kid has a nickname and a username.

**Copy respects the reader.** Same rules as expedition authoring: no "Let's have fun!", no exclamation point inflation, no talking down. The welcome screen for a kid says "Let's log your first find." That's it. The app's job is to get out of their way.

**The PIN is a handoff, not a security boundary.** A 4-digit PIN is defensible for kid accounts because: (a) the attack surface is narrow — you need the username AND the PIN AND physical proximity or phish, (b) the blast radius is small — losing a kid account doesn't expose financial info or meaningful PII, (c) anything stronger creates friction that defeats the use case. Parents and teachers get real passwords because they own the real accounts.

## What's out of scope for Phase 1

- Self-signup for kids (never — see product position).
- Friend discovery / social graph beyond the group.
- Multi-family / multi-class shared kids (a kid in both a family group and a class group). Deferred — requires a "primary group" concept on the kid's profile.
- Account transfer (parent-created kid moves to teacher-created flow or vice versa). Deferred — requires a migration workflow.
- SSO (Sign in with Apple, Sign in with Google). Deferred — complicates the kid-account flow because Apple and Google both require 13+ for their SSO products.
- Bulk student import from CSV for teachers. Deferred to Phase 2 — printable welcome sheet handles the Phase 1 closed beta.
- Age verification beyond self-attested age band. Deferred — age verification is hard, and our data-minimization posture means a wrong age band mostly affects which expeditions surface, not safety.
- Waitlist for non-invited users. The role picker has a "I want an invite" link that emails Brian. That's the whole Phase 1 waitlist system.

## Open decisions

These aren't resolved in this doc; pick one before building.

**Welcome-sheet delivery mode.** Print-at-home PDF vs bulk-email-to-parents. Print-at-home is simpler to build (no email deliverability issues, no bulk-email compliance); bulk email is simpler for teachers (no printer, no cut-and-distribute). Default recommendation: both, with PDF as the first built and email as Phase 1 Week 11 polish.

**Parent consent URL format.** Embedded-in-app-via-deep-link vs browser-only. Deep link is lower friction but requires the parent to have the app installed (which they might not — especially if they're a non-parent guardian receiving the welcome sheet). Browser-only is universal. Default recommendation: browser-only for consent, deep link for everything downstream.

**Kid-account lifetime if parent never logs back in.** If a parent creates a kid account, the kid uses it for a month, then the parent churns and never comes back — we still hold the kid's data. Proposed policy: if no adult from the family group has authenticated in 180 days, email the parent a "your account will be deleted in 30 days" notice, then delete. Defer the actual implementation to Phase 2; define the policy now.

**Teacher verification.** We accept any teacher signup as legitimate in Phase 1. If a bad actor claims to be a teacher, they could create a class of fake kids. Blast radius: limited, because each kid requires a unique parent consent email, which limits scale-of-abuse to "how many fake parent emails can you fake." Post-Phase-1, options include domain-based verification (teacher email must match a school domain), upload-proof-of-employment, or paid-only-teacher tiers. Not decided.
