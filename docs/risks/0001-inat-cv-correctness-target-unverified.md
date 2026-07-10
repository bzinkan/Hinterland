# Risk 0001: Optional post-clean iNaturalist CV is not approved or benchmarked

- **Status:** Open; feature disabled
- **Date filed:** 2026-05-10
- **Updated:** 2026-07-09 for ADR 0015
- **Owner:** Brian (legal disclosure, account setup, and real-photo benchmark)
- **Source:** Phase 7 50-photo correctness exit criterion

## Current Decision

Immediate kid-facing identification uses the project-owned taxonomy catalog,
manual display text, or Unknown. There is no pre-save image CV and no live taxon
fallback to iNaturalist.

`POST /v1/observations/{id}/identify` is an optional post-clean convenience. It
may run only after the server verifies the canonical photo is clean or
adult-approved. Pending, pilot-private, quarantine, failed, rejected, and
deleted states fail closed. `INAT_CV_ENABLED`,
`INAT_CV_DISCLOSURE_APPROVED`, and `INAT_CV_BENCHMARK_APPROVED` all default
false; photo egress requires all three.

Public iNaturalist observation submission is outside this risk and remains
disabled for W1 and closed beta. A project account/token does not authorize
publishing a child's observation.

## What Is Implemented

- The post-clean endpoint verifies owner, observation, photo, and moderation
  state before photo egress.
- Provider outage, throttling, malformed responses, and transport errors fall
  back without blocking catalog/manual/Unknown.
- The runtime taxonomy catalog supplies canonical names and Dex IDs without a
  child-request call to iNaturalist.
- Egress has independent default-deny gates; a token alone cannot enable it.

## What Is Not Verified

1. Legal/privacy disclosure and adult approval for sending an approved child
   photo to iNaturalist's CV service.
2. Top-three accuracy on 50 representative kid-style photos.
3. Token scope, rate limit, and class-burst behavior.
4. Staged cache behavior keyed by canonical photo SHA-256 and model version.

## Benchmark Contract

Use 50 consented, non-sensitive outdoor photos with reviewed ground-truth taxon
IDs. Do not use W1 pilot-private photos. Record only benchmark ID, expected
taxon, returned IDs/scores/model version, and hit/miss; keep bytes in the
approved private test location.

Starting target: at least 70% correct taxon in the top three at an appropriate
taxonomic level. Product/legal review must approve dataset and target before the
first live probe.

## Unblock Checklist

- [ ] Approve disclosure, consent language, retention, and provider data terms.
- [ ] Obtain minimum-scope credentials in isolated Hinterland Key Vault; do not
      wire public-submit permissions.
- [ ] Verify suggestion cache by canonical hash plus model version.
- [ ] Capture and label the reviewed 50-photo benchmark.
- [ ] Run against a staging post-clean endpoint and record top-three results.
- [ ] Verify unavailable, throttled, malformed, and outage paths fall back.
- [ ] Verify no non-clean state can reach the provider.
- [ ] Enable only after accuracy and legal gates pass.

## Mitigation

The core Observation experience does not depend on CV. Reviewed catalog packs
plus server search support canonical selections and Dex progress; manual text
and Unknown always save without creating a Dex species.
