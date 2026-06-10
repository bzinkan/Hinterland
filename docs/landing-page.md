# Dragonfly Landing Page Plan

This document defines the first-pass planning and copy foundation for a
legitimate public landing page at `https://dragonfly-app.net`.

It is a planning document only. It does not implement the website, add
dependencies, or finalize legal copy. Legal, privacy, store, and compliance
language must be reviewed before any production/public launch claim is made.

Before using the public landing/support URLs in Google Play Console, run the
final [`landing-pre-play-checklist.md`](landing-pre-play-checklist.md) and the
deployment smoke in [`landing-deploy-runbook.md`](landing-deploy-runbook.md).

## 1. Page purpose

The landing page is a parent-facing trust page. It should quickly explain what
Dragonfly is, why it exists, and how adults manage kid accounts.

It is also a Google Play support surface. Reviewers need a live, coherent site
that matches the app's store declarations, privacy posture, support links, and
audience.

It is a pilot signup surface for a controlled Android pilot. Interested adults
should understand that participation is invite-only, supervised, and limited.

It is not a kid-facing game teaser. The page should not hype collection,
competition, rarity, streaks, social status, or a fantasy world as the main
promise. The public page speaks to adults; the app experience can still feel
warm and magical once a child is inside an adult-managed account.

## 2. Target audiences

### Parents and guardians

Parents need to know what the app does, what data is collected, who manages the
kid account, how photos and location are used, what safety boundaries exist,
and how to contact a real operator.

### Teachers and group leaders

Teachers need to know that Dragonfly is invite-only, adult-managed, group-based,
and designed around real outdoor observation rather than classroom screen time.
They also need confidence that kids do not self-register or message each other.

### Google Play reviewers

Reviewers need clear public support, privacy, terms, and contact surfaces. The
page should match Play Internal Testing posture: adult-supervised pilot,
coarse/foreground location for Android internal testing, no ads, no public chat,
no direct messages, no public kid profiles, and no overclaims.

### Early pilot families

Pilot families need a simple explanation of what will happen, what adults will
be asked to supervise, what information the public page will collect, and what
will not be collected on the public page.

## 3. Page structure

Use this exact first-pass page structure:

1. Header
2. Hero
3. How Dragonfly works
4. Meet The Sanctuary
5. Built for families, with kids managed by adults
6. For families / for teachers
7. Closed Android pilot
8. FAQ
9. Footer

## 4. Approved core positioning

Primary positioning:

> Dragonfly is a field app for curious explorers of all ages. People photograph
> plants, animals, fungi, and other living things they find outdoors, build a
> personal Dex, complete nature expeditions, and watch their own Sanctuary grow
> from real observations.

Use this as the source of truth for page copy, store support copy, and pilot
emails unless a later PR updates this document.

## 5. Hero copy options

### Option A: Trust-first

**Headline:** A nature field app for all ages, with kids managed by adults.

**Subheadline:** Dragonfly helps curious explorers of all ages photograph real
living things outside, build a personal Dex, complete nature expeditions, and
grow a private Sanctuary from their own observations.

**Best for:** parents, guardians, Google Play reviewers, and a first public
landing page where trust matters more than spectacle.

### Option B: More playful

**Headline:** Every real-world find helps a Sanctuary grow.

**Subheadline:** Kids head outside, photograph plants, animals, fungi, and other
living things, then watch their personal Dragonfly world respond to what they
really observed.

**Best for:** a later, more visual page once the Sanctuary art is stronger and
the pilot/legal footing is complete.

### Option C: Teacher/classroom

**Headline:** Turn outdoor observations into a classroom nature log.

**Subheadline:** Dragonfly gives teachers and group leaders an invite-only way
for groups to record real organisms, complete expeditions, and build confidence
as naturalists.

**Best for:** a future teacher-specific page or `/teachers` route.

### Recommendation

Use Option A for the first public landing page. It is the clearest, safest, and
most reviewer-friendly choice. It still leaves room for warmth, but it leads
with adult management and real-world learning instead of collection mechanics.

## 6. Final recommended landing copy

### Header

Navigation:

- Dragonfly
- How it works
- Safety
- Pilot
- FAQ
- Privacy

Header CTA:

Join the pilot

### Hero headline

A nature field app for all ages, with kids managed by adults.

### Hero subheadline

Dragonfly is a field app for curious explorers of all ages. People photograph
plants, animals, fungi, and other living things they find outdoors, build a
personal Dex, complete nature expeditions, and watch their own Sanctuary grow
from real observations.

### Primary CTA

Ask about the Android pilot

### Secondary CTA

Read the privacy draft

### Trust line

No ads. No public chat. No direct messages. Kid accounts are created and managed
by a parent or teacher.

### How it works section

**Heading:** How Dragonfly works

**Intro:** Dragonfly turns a walk outside into a simple field routine: notice
something living, take a photo, choose what it might be, and let the app save
the observation.

**Step 1: Go outside and look closely**

Kids use Dragonfly while exploring a yard, park, school garden, trail, or other
supervised outdoor space.

**Step 2: Photograph a living thing**

The app is built around plants, animals, fungi, and other organisms. Dragonfly
does not ask kids to photograph people as the subject of an observation.

**Step 3: Build a personal Dex**

Each confirmed observation can become part of the kid's private field record:
what they found, when they found it, and how it helped their Dex grow.

**Step 4: Complete nature expeditions**

Expeditions give kids gentle goals, like looking for leaves, insects, birds, or
signs of habitat. They are authored content, not live AI prompts.

### Sanctuary section

**Heading:** Meet The Sanctuary

**Copy:** The Sanctuary is a private in-app habitat scene that grows from a
kid's real observations. A plant, bird, bug, mushroom, or pond creature can
change what appears there, but only after the kid records something from the
real world.

The Sanctuary is not a public profile, leaderboard, map, chat room, or social
feed. It is a quiet place for a kid to see their own field log reflected back as
a living scene.

**Short card copy:**

- Private to each kid
- Built from real observations
- No public sharing or kid-to-kid messaging
- No streaks, loot boxes, ads, or purchases

### Safety section

**Heading:** Built for families, with kids managed by adults

**Copy:** Dragonfly is designed for curious explorers of all ages, while kid
account setup belongs to adults. Parents or teachers create kid accounts, manage
group access, and supervise pilot participation.

**Trust points:**

- Kid accounts do not use kid email addresses.
- The Android Internal Testing build uses approximate/coarse foreground
  location for observations.
- Observation photos are stored privately unless and until a later approved
  contribution flow is enabled.
- iNaturalist public submission is off for the W1 Internal Testing pilot.
- There are no ads, public chat, direct messages, or public kid profiles.
- The pilot is invite-only and adult-supervised.

### For families / for teachers section

**Heading:** For families and teachers

**Families copy:** Families can use Dragonfly as a supervised way to help kids
notice more of the living world around them. A parent creates and manages the
kid account, then the kid uses the mobile app to record observations.

**Teachers copy:** Teachers and group leaders can use Dragonfly for controlled
pilot groups, nature clubs, school gardens, and field walks. Teacher use stays
invite-only while the app is being tested with real families.

**Shared note:** Dragonfly is still in pilot. We are intentionally starting
small so safety, support, and account-deletion workflows can be verified before
broader release.

### Pilot section

**Heading:** Closed Android pilot

**Copy:** Dragonfly is preparing for a small Android Internal Testing pilot with
known families. The pilot is adult-supervised, invite-only, and focused on
verifying the real setup flow: parent web setup, kid QR sign-in, one outdoor
observation, Dex/reward behavior, Sanctuary reveal, and adult review.

**CTA copy:** Interested in helping test Dragonfly with your family or group?
Send a note and we will follow up when a supervised pilot slot is available.

**Button:** Ask about the pilot

**Low-risk note:** The pilot interest form should collect adult contact details
only. Do not collect kid names on the public landing page.

### Footer copy

Dragonfly is in private pilot. Public launch and store availability will come
after safety, privacy, legal, and store-review gates are complete.

Footer links:

- Privacy
- Terms
- Support
- Contact

Footer contact:

- Support: `support@dragonfly-app.net`
- Privacy: `privacy@dragonfly-app.net`

### FAQ answers

**Is Dragonfly available now?**

Not publicly. Dragonfly is preparing for a small, adult-supervised Android pilot
through Google Play Internal Testing. Broader release depends on legal, privacy,
store, and field-testing gates.

**Who is Dragonfly for?**

Dragonfly is designed for curious explorers of all ages. Kid accounts are
managed by adults: parents, guardians, teachers, and group leaders.

**Can kids create their own accounts?**

No. Kid accounts are created and managed by a parent or teacher. Kids do not
self-register with email or password.

**Does Dragonfly have chat or direct messages?**

No. Phase 1 has no public chat, no direct messages, no kid-to-kid free text, and
no public kid profiles.

**Does Dragonfly collect location?**

Dragonfly uses observation location so a find can be recorded as a real field
observation. The Android Internal Testing build uses approximate/coarse
foreground location, not fine location. Do not say "no location collected."

**Are observations posted to iNaturalist?**

Not during the W1 Internal Testing pilot. Dragonfly may use iNaturalist for
species suggestions, but public submission is off unless a later, approved
adult-mediated contribution flow is enabled.

**What is The Sanctuary?**

The Sanctuary is a private in-app habitat scene that grows from a kid's real
observations. It is not a public profile, social feed, or map.

**Is the Privacy Policy final?**

No. The current privacy and terms pages can be accurate pilot-facing drafts, but
they must not claim attorney review until that review has happened.

**How do I ask about the pilot?**

Use the pilot contact CTA or email support. The public page should collect only
adult contact information and pilot logistics, not kid names.

## 7. Safety/trust copy rules

### Allowed

- No ads.
- No public chat.
- No direct messages.
- No public kid profiles.
- Parent- or teacher-managed accounts.
- Invite-only beta groups.
- Adult-supervised pilot.
- Approximate/coarse foreground location for the Android Internal Testing build.
- iNaturalist public submission is off for W1 Internal Testing.
- Privacy and Terms drafts exist, with final legal review still pending.

### Avoid unless legally/product verified

- "COPPA compliant"
- "Google Play Families approved"
- "Fully moderated in real time"
- "Safe for all classrooms"
- "Submitted automatically to iNaturalist"
- "Anonymous"
- "No location collected" if observation location is still collected
- "Reviewed by counsel" unless true
- "Teacher Approved" unless Google has approved that badge/program status
- "Available on Google Play" until the public Play listing is live

## 8. Visual direction

Use a warm off-white background. The page should feel like a field notebook, not
a dashboard, gamer site, or collectible franchise.

Use leaf green, moss, and sky blue accents. A small amount of warm yellow or
soft clay can support calls to action, but avoid a one-note palette.

Use rounded cards for repeated trust points, FAQ items, and audience blocks.
Cards should feel sturdy and calm, not glossy or toy-like.

Use an illustrated nature feel. Soft SVG or line-art is acceptable before final
illustrations exist. Early art can show leaves, a field notebook, a phone, a
dragonfly guide, habitat shapes, or a gentle Sanctuary scene.

Avoid:

- dark gamer style
- neon palettes
- "collect them all" framing
- fake Google Play badges before the Play listing is live
- public leaderboard or social screenshots
- fake app-store review badges
- exaggerated science claims
- rarity-as-status visuals

Visuals should support the adult trust story first: real outdoors, adult
management, privacy boundaries, and a personal nature record.

## 9. Privacy / Terms / Support requirements

Needed URLs:

- `/privacy`
- `/terms`
- `/support`
- `/contact` or a clear contact block

Current implementation target: `web/public/` already serves the static
landing/legal pages, with Firebase Hosting and Azure Static Web Apps deploy
workflows present. A follow-up implementation PR should update those static
files rather than create a new app framework.

Legal copy must be reviewed before production/public launch. For now, pages can
be accurate pilot-facing drafts, but they must not claim attorney review,
Google Play Families approval, COPPA compliance, or production readiness unless
those claims are true.

The footer should make support easy to find:

- `support@dragonfly-app.net` for general help
- `privacy@dragonfly-app.net` for privacy requests

Before closed/public store tracks, confirm both inboxes are live and monitored.

## 10. Pilot CTA plan

Use a low-risk pilot CTA for the first implementation.

Recommended first pass: a `mailto:` link to `support@dragonfly-app.net` with
the pre-filled subject `Dragonfly pilot access request`.

The first mailto body should ask only for adult contact and pilot logistics:

- Parent/guardian name
- Email
- Number of kids
- Kids' age range
- Android phone available?
- Are you willing to test with your child present?
- Anything we should know?

Place this note near every main pilot CTA:

> Please do not include your child's full name in this request.

Use this confirmation/help copy near the pilot CTA:

> Dragonfly is in a small supervised pilot. We'll reply if we can include your
> family in the next test group.

Acceptable second pass: a simple static form that submits to an operator-owned
endpoint or low-risk form backend after privacy review. Do not add a tracking
SDK just to support pilot interest.

Do not embed third-party analytics on the first public landing page.

Do not collect kid names on the public landing page.

If a form is added later, collect only:

- parent/guardian name
- email
- number of kids
- kids' age range
- Android availability
- willingness to test with the adult present
- consent to be contacted about a supervised pilot, if explicit consent text is
  added to that future form

Optional free-text should be framed for adult logistics only, such as:

> Anything we should know about your interest in the Dragonfly pilot?

Avoid collecting:

- kid names
- school rosters
- exact addresses
- phone numbers unless there is a real operational need
- photos
- location
- student IDs

## 11. Implementation plan

### PR 1: Static page shell

Update `web/public/index.html` to the approved structure in this document.
Keep it static HTML/CSS. Do not add dependencies. Replace overclaiming iNat copy
with pilot-safe language. Ensure the page remains deployable by the existing
Firebase Hosting and Azure Static Web Apps workflows.

### PR 2: Content pages

Update or add `/privacy`, `/terms`, `/support`, and `/contact` surfaces under
`web/public/`. The privacy and terms pages should remain clearly marked as
drafts until legal review is complete. `/support` can be a simple support page
with email, response expectations, and account-deletion request instructions.

### PR 3: Pilot CTA

Add the pilot CTA. Start with `mailto:` unless a reviewed static form endpoint
exists. If a form is added, do not include analytics, tracking pixels, or kid
name fields.

### PR 4: SEO and accessibility

Add page metadata, Open Graph/Twitter cards, semantic headings, accessible link
labels, focus states, high-contrast checks, reduced-motion-safe decorative
assets, and mobile viewport QA. Use real copy and alt text; avoid fake app-store
badges.

The public hero copy can remain all-ages while search/social metadata may
describe the current Google Play review and pilot audience more narrowly as
kids ages 9-12. Do not use SEO metadata to claim public availability, Google
Play Families approval, COPPA compliance, automatic iNaturalist submission, or
legal review before those are true.

### PR 5: Deploy verification

Verify the static landing site deploys cleanly to the active hosting path for
`dragonfly-app.net` and `www.dragonfly-app.net`. Confirm:

- `/` returns HTTP 200
- `/privacy` returns HTTP 200
- `/terms` returns HTTP 200
- `/support` returns HTTP 200
- `/contact` or the contact block is reachable
- footer links resolve
- no draft page claims attorney review
- no public page says automatic iNaturalist submission is enabled
- no public page says location is not collected

## PR summary checklist

The PR that introduces this document should summarize:

- recommended landing-page structure
- recommended hero headline: "A nature field app for all ages, with kids managed by adults."
- key trust messages: no ads, no public chat, no DMs, no public kid profiles,
  adult-managed accounts, invite-only supervised pilot, iNat public submission
  off for W1
- legal/compliance copy that must be avoided, especially "COPPA compliant,"
  "Google Play Families approved," "fully moderated in real time," "submitted
  automatically to iNaturalist," "anonymous," and "no location collected"
- next implementation PRs: static page shell, content pages, pilot CTA,
  SEO/accessibility, deploy verification
