# ADR 0009: Cloud Vision SafeSearch as the moderation provider

- **Status:** Accepted
- **Date:** 2026-05-09
- **Deciders:** Solo author
- **Related:** ADR 0002 (LLMs are author-time, not runtime), ADR 0005 (GCP target architecture), ADR 0007 (Multi-agent AI is internal/adult-only). Closes the open follow-up flagged in `docs/moderation.md` and `docs/architecture.md`.

> Numbering note: ADR 0008 referenced "0009, 0010" as future per-environment Cloud Run policy ADRs. Those env ADRs will take the next sequential numbers when staging/prod come online — no number is reserved for them in advance. This ADR is 0009 because it is the next decision actually being made.

## Context

`docs/moderation.md` specifies the full pipeline: photos arrive in `gs://hinterland-photos-<env>-<project>/pending/<obs_id>.jpg` via signed PUT, an Eventarc `google.cloud.storage.object.v1.finalized` trigger fires a moderation Cloud Run service, and that service moves the object to `observations/` (clean) or `quarantine/` (flagged) and writes a `review_queue` row in the flag case. None of that depends on the provider.

What is *not* yet decided is which provider the moderation Cloud Run service calls. `docs/moderation.md` flags Cloud Vision SafeSearch as the leading candidate but defers the choice to "a follow-up ADR." Phase 8 (async workers, per `AGENTS.md`) is blocked on this decision because the worker can't be implemented until the provider's request/response shape is settled.

The choice has to satisfy:

1. **Kid-facing safety.** Photos that contain adult, violent, or sexually suggestive content must not reach the rest of the app and must not be submitted to iNaturalist. False negatives are far worse than false positives — a teacher reviewing a borderline animal-injury photo is fine; a passed adult photo is a beta-killer.
2. **Out-of-band, latency-tolerant.** The kid's `POST /v1/observations` returns success before moderation runs. Moderation runs on the order of seconds. Provider p99 latency under ~5s is fine; we are not on the hot path. (`docs/moderation.md`, "Design decisions baked into this pipeline.")
3. **GCP-native preferred.** Per ADR 0005, we run on GCP. Cross-cloud calls add IAM federation, network egress cost, and an extra failure mode without product benefit.
4. **No kid-facing runtime LLM.** ADR 0002 and ADR 0007 forbid live LLM calls on the kid request path. The moderation worker is *not* on the kid's request path (it's an Eventarc-triggered post-upload worker), so an LLM call here is not strictly forbidden by either ADR — but the spirit of those ADRs (no opaque, prompt-engineered classifier in the safety boundary for child users) still applies. A purpose-built classifier with a labeled response is preferred to a vision-LLM here.
5. **Auditable.** When a teacher reviews a quarantined photo, the UI needs to surface *why* it was flagged. Provider response must include named labels and confidence, not opaque scores.
6. **Tunable without redeploy.** `docs/moderation.md` already specifies `HINTERLAND_MODERATION_THRESHOLD` and `HINTERLAND_MODERATION_FLAG_LABELS` env vars. The provider's response shape has to fit that contract.

## Decision

Use **Cloud Vision SafeSearch** (`images.annotate` with `SAFE_SEARCH_DETECTION`) as the runtime moderation gate for kid-uploaded photos.

Default flag rule:

| SafeSearch label | Threshold to flag           |
|------------------|-----------------------------|
| `adult`          | `LIKELY` or `VERY_LIKELY`   |
| `violence`       | `LIKELY` or `VERY_LIKELY`   |
| `racy`           | `LIKELY` or `VERY_LIKELY`   |
| `medical`        | `VERY_LIKELY` only          |
| `spoof`          | ignored                     |

Rationale for the per-label thresholds:

- **`adult`, `violence`, `racy`**: standard kid-safety floor. `LIKELY` matches Google's published safe-content guidance and aligns with the conservative posture documented in `docs/moderation.md` ("False negatives are far worse than false positives").
- **`medical`**: kids photographing nature will sometimes capture animal injuries, road-killed wildlife, or specimens that read as "medical content" to SafeSearch. Flagging at `LIKELY` over-quarantines legitimate observations. `VERY_LIKELY` keeps real medical imagery (surgical, gore, etc.) in the gate without sending every roadside-skunk photo to teacher review.
- **`spoof`**: SafeSearch's `spoof` label flags meme-modified or doctored images. It is not a child-safety category. Surfacing it here would be category drift; if anti-meme rules ever matter (Phase 2+), they get their own ADR.

These defaults populate `HINTERLAND_MODERATION_FLAG_LABELS` and `HINTERLAND_MODERATION_THRESHOLD` (the threshold maps to the SafeSearch likelihood enum). Both are revision-level env vars on the moderation Cloud Run service so we can tighten or loosen without a code change — see `docs/moderation.md` "Moderation provider configuration."

### Region

The moderation Cloud Run service runs in `us-central1` (matches the API and Cloud SQL). Vision API requests are made via the regional endpoint `us-central1-vision.googleapis.com` so the kid's image bytes do not leave the US region during the moderation call. This matches the rest of the data plane and keeps the COPPA conversation simple.

### Failure handling

Moderation failures (Vision 5xx, throttle, network fault) are **non-2xx returns from the moderation Cloud Run service**, which causes Eventarc to retry. After retry exhaustion, the trigger DLQs to a Pub/Sub topic monitored per `docs/runbook.md`. The pending photo is swept by the GCS lifecycle rule after 24h. This matches the "do not default-allow on moderation failure" decision in `docs/moderation.md` and is unchanged by the provider choice.

## Consequences

### Positive

- **Native to GCP.** Same project, same IAM, same Cloud Logging. The runtime SA gets `roles/serviceusage.serviceUsageConsumer` and Cloud Vision API access via the project enablement; no vendor onboarding.
- **Predictable, low cost.** Cloud Vision is ~$1.50 per 1000 SafeSearch annotations. At a beta target of 1000 observations/day, moderation costs ~$45/month — small enough to ignore in budgeting and small enough that the alarm budget in ADR 0005 covers a 10x spike before it becomes a real signal.
- **Five-level likelihood scale.** `VERY_UNLIKELY` → `VERY_LIKELY` gives room to tune thresholds without changing categories. Per-label thresholds (above) are easier to reason about than a single numeric cutoff.
- **Purpose-built classifier.** SafeSearch is trained for safety classification, not retrofitted from a general visual model. Response is a fixed set of named labels — auditable and stable. A teacher reviewing a quarantined photo sees "flagged: violence=LIKELY" not "score 0.87."
- **No prompt engineering, no ADR 0002/0007 surface area.** The moderation gate is not an LLM, so the "kid-facing LLM" invariant is unambiguously preserved.

### Negative

- **Not kid-photo-specialized.** SafeSearch's training set is general-purpose. Edge cases relevant to citizen science — animal injuries, snake skins, dead specimens, taxidermy — may produce false positives that show up as teacher review queue load. Mitigation: the `medical` `VERY_LIKELY` threshold above plus the teacher review path. If false-positive rate during early dogfood is high enough to be a teacher-time tax, the response is to add a *second-stage* per-label override (e.g., "if `violence` is the only flag and iNat CV high-confidence-classifies the photo as a real species, soft-flag instead of quarantine"). That is a Phase 8 implementation choice, not a provider switch.
- **No "off-topic" category.** A kid's photo of their bedroom or homework passes SafeSearch but isn't a nature observation. SafeSearch will never catch this and is the wrong tool to ask. iNat CV (Phase 7) is the natural off-topic filter — low CV confidence across the entire candidate set is a "this isn't a species" signal. Treat the off-topic problem as a separate Phase 7+ feature, not a moderation gap.
- **Vendor lock at the gate.** Moving to a different provider later means rewriting the moderation Cloud Run service's call site, threshold mapping, and label set. The pipeline structure (GCS prefixes, Eventarc trigger, `review_queue`, Cloud Tasks for iNat submit) is provider-agnostic by design (`docs/moderation.md`), so a swap is contained — but it's not a one-line env-var change either.
- **No fine-tuning or custom labels.** SafeSearch is a black-box managed API. We can't train it to be better at nature-photo edge cases. If we hit a recurring class of false positives that the per-label thresholds and second-stage filter can't handle, the upgrade path is a different provider (Hive, Sightengine) that supports custom moderation rules — not "make SafeSearch smarter."

### Neutral

- **No raw-response retention.** The moderation Cloud Run service stores the chosen labels + likelihood on the `observations` row (`moderation_labels`) per `docs/moderation.md`. The full Vision API JSON response is not retained — it would be PII-adjacent (it's tied to a kid's photo) without product value beyond the label list. This matches the data-minimization posture appropriate for a kids' app.
- **Cost monitoring.** Add a Cloud Monitoring alarm at 2x the expected monthly Vision API call volume. Beyond a 2x spike, either we shipped a feature that doubled photo volume (intentional, dismiss the alarm) or something is retrying in a loop (real signal). Defer to Phase 8 as part of the moderation worker observability bundle.

## Alternatives considered

### AWS Rekognition `DetectModerationLabels`

**Rejected.** Per ADR 0005 we run on GCP. Bringing AWS in for moderation alone means a second cloud account, IAM federation, network egress for every photo from GCS to S3 (or AWS-side ingestion), a second cost line, and a second incident-response surface. Rekognition's moderation taxonomy is more granular than SafeSearch (it has explicit sub-labels under "violence" etc.), but that granularity is wasted unless we're surfacing the sub-label in the teacher UI, which `docs/moderation.md` doesn't require.

### Hive Moderation

**Considered, deferred.** Hive supports custom moderation rules, has a richer label taxonomy, and is widely used by kids' platforms. Two reasons not to adopt now: (a) it's a third-party SaaS — additional vendor risk, separate billing, separate SLA; (b) at ~$2.50 per 1000 it's ~70% more expensive than SafeSearch with no measurable kid-safety win until we have evidence SafeSearch is systematically wrong for nature photos. Reconsider after 30+ days of beta data if SafeSearch's false-positive rate for nature-edge-cases is high enough to be a teacher-time tax — the swap path is one-service-rewrite per the `docs/moderation.md` provider-agnostic structure.

### Sightengine

**Rejected.** Same shape as Hive (third-party SaaS, comparable price, comparable taxonomy) without a clear differentiator for our use case. If we ever leave SafeSearch for a third-party, Hive has more public usage in kid/family-app contexts to point at, which matters for due diligence.

### Open-source models (NSFWJS, OpenNSFW2, etc.) self-hosted

**Rejected.** Per-image cost goes to ~zero, but: (a) ops surface — model serving, GPU vs CPU latency tradeoffs, drift, retraining; (b) the OpenNSFW reference model is years stale and doesn't cover violence/medical at all; (c) we'd own the safety-classification accuracy curve for a kids' app, which is a serious liability without a moderation-research hire. At the modest scale of beta (~1000 obs/day = ~$45/month for SafeSearch) the cost we'd save is a rounding error and the risk we'd take on is enormous.

### Vision-LLM (Gemini 1.5 Pro vision, Claude 3.5 Sonnet vision, GPT-4o vision)

**Rejected.** ADRs 0002 and 0007 narrowly forbid kid-facing runtime LLMs, and while the moderation worker is technically off the kid request path, applying a prompt-engineered LLM as the *safety boundary* for child-uploaded content is exactly the failure mode those ADRs were written to prevent. Beyond the ADR alignment: cost is 50–100x SafeSearch per image, p99 latency is several seconds, the response is free-text that has to be parsed and is non-deterministic across requests, and the "audit trail" is a prompt + a model version that may be deprecated. Vision-LLM is the right tool for adult-side batch re-review or for explainability ("why did SafeSearch flag this"), and that use case stays open per ADR 0007 (internal/adult-only). It is not the runtime gate.

### Run SafeSearch *plus* a second provider in shadow mode

**Considered, deferred.** Tee-ing every photo to a second provider (Hive/Vision-LLM) and comparing labels would give us the false-positive/false-negative data to know when to switch. Not worth the cost or complexity until we have a concrete reason to suspect SafeSearch is wrong. Revisit if dogfood reveals a pattern.

## Follow-ups

- **Phase 8 implementation.** Build the moderation Cloud Run service against SafeSearch. The interface in `docs/moderation.md` ("Moderation provider configuration" and "What the moderation Cloud Run service writes") is the spec.
- **Default env vars.** When the moderation Cloud Run service ships, set:
  - `HINTERLAND_MODERATION_FLAG_LABELS = ["adult","violence","racy","medical"]`
  - `HINTERLAND_MODERATION_THRESHOLD = "LIKELY"` (with the `medical=VERY_LIKELY` override applied in code, since SafeSearch is per-label)
  Document the override in the moderation service README.
- **Regional endpoint.** Use `us-central1-vision.googleapis.com` for the Vision API call. Verify in the worker's startup log that the configured endpoint is regional, not global.
- **Cost alarm.** Add a Cloud Monitoring alarm at 2x expected monthly Vision API spend (initial expectation ~$45 → alarm at $90).
- **False-positive review.** After 30 days of dogfood, pull the `review_queue` rows resolved as "approved on review" and tabulate by SafeSearch label. If `medical` is dominant, raise its threshold further or carve out an explicit nature-photo exemption. If `violence` is dominant, consider Hive shadow-mode.
- **Retention.** Store only the chosen labels + likelihood values on the `observations` row, not the raw Vision response JSON. The teacher review UI reads from that field.
- **Privacy/COPPA.** Cloud Vision processes images per Google Cloud's data processing terms. Verify the dev project has the GCP Data Processing Addendum signed before beta opens; if not, that's an admin task before we send real kid photos through SafeSearch.
