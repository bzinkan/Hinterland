# Landing Pre-Play Review Checklist

Use this checklist before entering Hinterland's public landing/support URLs in
Google Play Console, sharing them with pilot parents, or relying on them during
app review. It is not legal advice. Re-check current Google Play policy and get
legal review before Closed, Open, or Production tracks.

Related docs:

- [`landing-page.md`](landing-page.md)
- [`landing-deploy-runbook.md`](landing-deploy-runbook.md)
- [`google-play-internal-testing.md`](google-play-internal-testing.md)
- [`app-store-compliance-checklist.md`](app-store-compliance-checklist.md)
- [`privacy-policy-DRAFT.md`](privacy-policy-DRAFT.md)
- [`risks/0005-beta-launch-human-action-items.md`](risks/0005-beta-launch-human-action-items.md)

## 1. Public URL Availability

- [ ] Homepage returns HTTP 200: `https://thehinterlandguide.app/`
- [ ] Privacy returns HTTP 200: `https://thehinterlandguide.app/privacy`
- [ ] Terms returns HTTP 200: `https://thehinterlandguide.app/terms`
- [ ] Support returns HTTP 200: `https://thehinterlandguide.app/support`
- [ ] Contact returns HTTP 200: `https://thehinterlandguide.app/contact`
- [ ] HTTPS certificate is valid for `thehinterlandguide.app`.
- [ ] HTTPS certificate is valid for `www.thehinterlandguide.app`, if that URL is
      used in any store field or support email.
- [ ] Public pages load without an auth wall, invite code, tester account, or
      app install.
- [ ] Public pages load in a private/incognito browser session.
- [ ] `cd web && npm run smoke:live` passes against `https://thehinterlandguide.app`.

## 2. Copy Accuracy

- [ ] No "COPPA compliant" claim appears unless lawyer-approved.
- [ ] No "Google Play approved," "Google Play Families approved," or
      "Teacher Approved" claim appears before approval is actually granted.
- [ ] No "fully moderated," "real-time moderation," or equivalent claim appears
      unless backend moderation is fully wired, operated, and documented.
- [ ] No "submitted to iNaturalist," "automatically submitted to iNaturalist,"
      or equivalent claim appears unless the iNat worker is configured and the
      contribution policy is reviewed.
- [ ] No "no location collected" or overly broad location reassurance appears
      while observation location is collected.
- [ ] Public/social copy matches app behavior: no public chat, no direct
      messages, no public kid profiles, no follower counts, and no open social
      discovery.
- [ ] Pilot/beta copy does not imply a broad public launch.
- [ ] Privacy and Terms pages remain clearly pilot-facing until legal review is
      complete.
- [ ] If copy says "adult-managed," "parent-managed," or "teacher-managed," the
      app flow still supports that claim.

## 3. Google Play Readiness

- [ ] Privacy URL is ready for the Play Console privacy policy field:
      `https://thehinterlandguide.app/privacy`.
- [ ] Support URL is ready for Play Console support/contact fields:
      `https://thehinterlandguide.app/support` or `https://thehinterlandguide.app/contact`.
- [ ] `support@thehinterlandguide.app` is live, monitored, and suitable for Play
      Console support contact.
- [ ] `privacy@thehinterlandguide.app` is live and monitored for privacy requests.
- [ ] App name in Play Console matches the current listing plan. Production
      listing name: `Hinterland` (renamed per ADR 0013; update the Console
      listing if it was created as `Hinterland`); internal tester device
      label is `Hinterland Internal` from the `play-internal` build.
- [ ] Package-name caution is understood: the first AAB uploaded to the
      production-intended Play Console app must use `app.thehinterlandguide`, not
      `app.thehinterlandguide.dev` or `app.thehinterlandguide.staging`.
- [ ] Data Safety draft answers align with landing/privacy copy: photos,
      approximate location, user content/species selection, adult email, no ads,
      no tracking.
- [ ] Target Audience / Families policy risks are acknowledged before any track
      that requires those declarations.
- [ ] Current Google Play policies are re-verified before Closed, Open, or
      Production submission.
- [ ] No fake Play Store badge appears before a public Play listing exists.

## 4. Pilot Readiness

- [ ] `Request pilot access` CTA works.
- [ ] CTA opens an email to `support@thehinterlandguide.app`.
- [ ] CTA subject is `Hinterland pilot access request`.
- [ ] CTA body asks for adult contact and pilot logistics only.
- [ ] CTA does not request a child's full name.
- [ ] Nearby CTA copy says not to include a child's full name.
- [ ] Adult-supervised pilot language is visible on the homepage.
- [ ] Known-family / invited-family / limited-pilot posture is visible.
- [ ] Android Internal Testing status is visible.
- [ ] No public rollout, public Play availability, or production-availability
      language appears before those claims are true.
- [ ] No fake app-store badges, review badges, certification badges, or
      approval badges are shown.

## 5. Technical Checks

- [ ] Mobile viewport checked at 360px wide.
- [ ] Mobile viewport checked at 390px wide.
- [ ] Tablet viewport checked.
- [ ] Desktop viewport checked.
- [ ] Header navigation does not overflow or hide primary links.
- [ ] Hero text and CTA buttons do not clip or create horizontal scroll.
- [ ] Footer links and support/privacy email addresses wrap legibly.
- [ ] Link preview metadata is present: title, description, Open Graph, Twitter
      card, and social image.
- [ ] `https://thehinterlandguide.app/sitemap.xml` includes homepage, privacy,
      terms, support, and contact URLs.
- [ ] `https://thehinterlandguide.app/robots.txt` allows normal indexing and points
      at the sitemap.
- [ ] Favicon loads.
- [ ] Touch icon / app icon metadata exists.
- [ ] Page load is acceptable on a normal mobile connection.
- [ ] Keyboard navigation works through header, CTAs, body links, and footer.
- [ ] Focus states are visible.
- [ ] Color contrast passes a basic accessibility review.
- [ ] Reduced-motion behavior is acceptable if animations/transitions are
      present.
- [ ] `cd web && npm run check` passes.
- [ ] `cd web && npm run smoke:live` passes.

## 6. Manual Sign-Off Table

Use status values such as `not started`, `in progress`, `blocked`, `passed`, or
`not applicable`.

| item | owner | status | date | notes |
|---|---|---|---|---|
| Public URL availability |  | not started |  |  |
| HTTPS / no auth wall |  | not started |  |  |
| Landing copy accuracy |  | not started |  |  |
| Privacy URL ready for Play Console |  | not started |  |  |
| Terms URL ready for Play Console |  | not started |  |  |
| Support email live and monitored |  | not started |  |  |
| Privacy email live and monitored |  | not started |  |  |
| Data Safety copy alignment |  | not started |  |  |
| Target Audience / Families risk acknowledged |  | not started |  |  |
| Pilot CTA and no-child-name check |  | not started |  |  |
| Mobile viewport pass |  | not started |  |  |
| Desktop viewport pass |  | not started |  |  |
| Link preview metadata |  | not started |  |  |
| Sitemap / robots / favicon |  | not started |  |  |
| Keyboard navigation |  | not started |  |  |
| Color contrast |  | not started |  |  |
| `npm run check` |  | not started |  |  |
| `npm run smoke:live` |  | not started |  |  |
| Legal review before broader release |  | blocked |  | Required before Closed/Open/Production tracks. |
| Final Play Console policy re-check |  | not started |  | Re-check official policy at submission time. |
