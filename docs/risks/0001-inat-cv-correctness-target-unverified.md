# Risk 0001: iNat CV correctness target unverified

- **Status:** Scoped down via Option B (2026-06-04)
- **Date filed:** 2026-05-10
- **Source:** `AGENTS.md` Phase 7 exit criterion ("50 kid-style test observations achieve top-3 iNat CV correctness target **or a risk is filed**")
- **Owner:** Brian (requires real-world setup; can't be unblocked autonomously)

## 2026-06-04 — Option B decision

Outbound iNat submission of kid observations was reframed under
**Option B**: Dragonfly does NOT post kid observations to iNat while
the kid is under 13 (iNat's ToS requires users to be 13+).
Observations stay inside Dragonfly until the kid reaches 13 and uses
the Phase 3 age-13 iNat-claim flow to push their own back catalogue
under their own iNat account.

The `inat_submit_enabled` setting defaults to False (PR #6 of the
risk-closure series). The Service Bus iNat-submit pipeline ships
dormant: queues + workers + alerts stay provisioned so flipping the
flag back on is a zero-deploy operator action, but no outbox rows
are written by default.

What stays open in Risk 0001:

- iNat's read-only **CV identify** endpoint
  (`POST /v1/observations/{id}/identify`) -- still used during the
  submit flow to suggest a species. Its accuracy on kid photos is
  still untested.
- The OAuth token + 50-photo benchmark items below now apply only
  to the CV identify path, not to outbound observation submission.
  The token's rate-limit scope is narrower than originally planned.

The bullets about "iNat user account + project + outbound submit"
are descoped; only the CV identify subset remains relevant.

## What we have

`POST /v1/observations/{id}/identify` (PR #39) calls iNat's `/v1/computervision/score_image` server-side and returns the top-3 taxa. The integration is unit-tested against `respx`-mocked happy/5xx/4xx/transport-error paths -- the **wiring** is verified.

What is **not** verified:

1. iNat CV's actual top-3 accuracy on Hinterland-style kid photos (handheld, close-up, often partial subject, often poor framing).
2. Whether the dev iNat OAuth token we'll eventually configure has the right scopes for the CV endpoint.
3. Whether iNat's rate-limit budget under our access tier survives a class of 25 kids each shooting 10 observations in an hour.

## Why we can't verify autonomously

Three blockers:

1. **No iNat project account.** Hinterland needs an iNat user account + project so observations submitted via the iNat-submit worker (Phase 8) attribute to the right place. The same account holds the OAuth token for the CV endpoint. Account creation is a manual signup with email/captcha + a project request reviewed by iNat staff (typically a few days). See <https://www.inaturalist.org/projects/new>.
2. **No labeled kid-photo benchmark dataset.** "50 kid-style test observations" needs both photos and ground-truth taxon IDs. Easiest path: Brian (or a tester kid) takes 50 photos in a real outdoor session, labels them, and runs them through the endpoint. Manual + a few hours.
3. **Score threshold is undefined.** "Correctness target" isn't quantified in `AGENTS.md`. Reasonable starting point: **>=70% of observations have the correct taxon in the top 3** (looser than top-1 because kids often shoot things at family or genus level). Tighten after dogfood.

## Mitigation in the meantime

- The mobile picker (PR #42) gracefully handles `cv_unavailable=true` (no token configured) and presents the "Type my own" + "Skip" paths. Kids can submit observations with manual species or none -- the only thing missing without iNat CV is the convenience of a tap-to-pick suggestion list.
- The `species_cache` table fills lazily from iNat's public taxa endpoint (no auth required), so even without the CV token we get clean species names when the kid manually picks an iNat taxon.

## Production unblock checklist

- [ ] Register `dragonfly-app` iNat user account
- [ ] File project application; get approval
- [ ] Generate OAuth token, store in Secret Manager as `dragonfly-dev-inat-oauth-token`
- [ ] Wire the secret into Cloud Run as `DRAGONFLY_INAT_OAUTH_TOKEN`
- [ ] Capture a 50-photo benchmark in a real outdoor session
- [ ] Score against the deployed endpoint; record per-photo top-3 hit/miss in a CSV
- [ ] If the hit rate is >=70%, mark this risk closed with the result table
- [ ] If lower, file follow-ups on confidence threshold tuning, retry-with-a-second-shot UX, or evaluating a different CV provider (e.g. Pl@ntNet for vegetation)

## Related work needed before production rollout

(Captured here so the iNat-token follow-up doesn't get filed in isolation.)

- **Geocoding provider (PR #41):** `noop` in dev, `nominatim` works for a few requests but Nominatim's TOS disallow commercial use. Production needs Google Maps Geocoding API or self-hosted Nominatim. Add a `runbook.md` section for the cutover when staging is provisioned.
- **iNat submit worker (Phase 8):** ADR 0009-shaped Cloud Tasks queue + DLQ; depends on the same iNat token being available.
