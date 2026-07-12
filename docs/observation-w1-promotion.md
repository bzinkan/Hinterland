# Observation W1 Promotion

This is the release contract for Observation and Field Journal W1 testing. W1
is a supervised internal pilot, not a safety-reviewed closed beta. Its only
allowed moderation transition is `noop -> pilot_private`; real Azure Content
Safety, CV/photo-helper egress, and public iNaturalist submission remain off.

## Three promotion terms

- **W1-ready to start** means the dedicated server promotion passed, the exact
  Play Internal AAB passed the physical-device and fault tests, the adult dry
  run passed, and the evidence record has an explicit adult go/no-go decision.
- **W1 fully evidenced** means W1-ready plus one supervised family session, its
  post-session privacy/data audit, deployed dispatcher-p95 evidence, received
  alert-test evidence, and a recorded continuation decision.
- **Closed-beta promoted** means the later Content Safety, review/rebuild,
  photo-revocation, accessibility, performance, and 24-hour/25-submission gates
  have passed. W1 evidence alone never enables Content Safety.

## Protected GitHub environment

Create a GitHub environment named `w1-promotion` and configure at least one
required adult reviewer. The existing repository-scoped Azure OIDC secrets
remain available to ordinary deployments and are inherited by this protected
environment:

- `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, and `AZURE_SUBSCRIPTION_ID` for the
  federated deployment identity.

Store only the promotion-specific values as environment secrets:

- `HINTERLAND_SMOKE_ENTRA_BEARER`, a current token for the isolated test parent;
- `HINTERLAND_ALERT_EMAIL`, a monitored operational address.

The parent token must be a v2 access token for the `user.access` scope. Its
`aud` claim is the API client ID
`7dd9da3c-b7d6-45d4-955b-d7561c43f209`, not the scope URI. The workflow rejects
a missing, wrong-tenant, wrong-audience, wrong-scope, or nearly expired token. It creates a
throwaway kid through the real exact-policy, browser-bound consent proof,
canonical parent, server-enforced group/kid, and handoff path, then passes that
kid's session to the Observation canary in memory. The generated Central
hostname is the canonical revision target; the workflow also requires the
public DNS and TLS binding to resolve to that same Central app before the
protected canary starts. An email-only,
stored-but-unlinked, missing-proof, or stale-policy consent must fail the
promotion. Do not create a persistent kid bearer secret or persist the consent
nonce.

## Dedicated server promotion

Run `.github/workflows/observation-w1-promotion.yml` manually and type
`PROMOTE_OBSERVATION_W1`. Ordinary development deployment is intentionally
separate and does not constitute W1 promotion.

The promotion job performs this order:

1. verifies subscription, tenant, and isolated `hinterland-dev-rg` (and rejects
   `gordi-pilot-rg`), then proves the primary API is in
   `hinterland-cae-central-dev`/Central US, the rollback API and every job are
   in `hinterland-cae-dev`/East US, and no job was cloned into Central;
2. applies and verifies NoOp moderation, all CV gates false, public iNaturalist
   submission false, OAuth token absent, and Observation idempotency required
   on both API apps and every required job;
3. removes any iNaturalist-named job, verifies inherited Service Bus roles do
   not permit the runtime identity to use the inert iNaturalist queue, and
   enumerates both explicit system topics and resource-scoped Event Grid
   subscriptions sourced by photo storage;
4. if a direct BlobCreated moderation subscription is found, deletes it for
   containment and fails the run so an adult must investigate before rerun;
5. runs the full disposable-PostgreSQL Observation suite with 50 mixed
   Unknown/catalog samples through the real handler registry, builds one
   `repository@sha256:...` image, requires the W1 job
   inventory/identity/database-secret contract, and pins only the manual
   preflight/migration jobs. After migration and rebuild, it deploys that same
   digest to the Central primary API, East rollback API, and every East job;
6. runs read-only preflight and additive migrations, then—and only then—pins
   every scheduled consumer/job before taxonomy ingest, Expedition sync, and a
   bounded strict/drained derived-state rebuild;
7. applies and verifies the 24-hour upload, seven-day pilot-private, and 90-day
   held/rejected lifecycle rules;
8. requires the parent, landing apex, and landing `www` build markers to match
   the promotion SHA before API rollout. It then makes a direct, unauthenticated
   `GET /auth/callback?route_probe=...` against both the custom parent domain
   and its backing Static Web Apps origin. Both responses must be a non-
   redirected `200 text/html`, carry the callback security headers, contain the
   route sentinel, and have identical bodies. This callback check is a hard
   promotion stop: a skipped check, a 404, a redirect, a stale build marker, or
   differing response bytes fails the run before API rollout. The probe never
   sends an OAuth authorization code, state, token, cookie, email, or consent
   proof. The workflow then runs public health/readiness/JWKS probes; before any
   authenticated canary, both the revision URL and public API domain must
   accept an exact-origin parent consent CORS preflight from the custom parent
   domain and its backing Static Web Apps domain, while an unrelated origin
   must be denied (wildcards and landing-site origins do not qualify); it then
   runs the non-skipped parent, kid, Expedition, idempotent Observation, Field
   Journal, `pilot_private`, DTO, and signed-photo-denial canaries. The same
   throwaway kid then seeds 50 mixed Unknown/catalog/coarse-location
   observations. Promotion polls Log Analytics for every exact observation on
   the exact revision and immutable image, and fails on missing/duplicate/
   partial events or nearest-rank dispatcher p95 greater than or equal to
   300 ms. The verifier uses the protected environment's GitHub OIDC assertion
   to mint a short-lived Log Analytics data-plane token in memory; neither the
   assertion nor access token is written to the artifact, command line, or
   logs. Local operators retain the authenticated Azure CLI fallback;
9. runs the database health job in strict mode, requires empty moderation
   active/DLQ counts, provisions/verifies alerts, and sends an action-group test;
10. verifies every API/job setting and immutable image again, rechecks the web
    build markers, and repeats the same non-skippable callback probe before
    finalizing evidence.

The parent bundle must export a real `/auth/callback.html`, and the Static Web
Apps configuration may rewrite only `GET /auth/callback` to that file. Do not
add a broad `navigationFallback`: unknown parent paths must continue to return
404 so a missing release route cannot masquerade as a healthy app shell. CI
checks the source contract, and the parent deployment checks the exported
contract before upload and the two live origins after upload. Both the live
parent deployment and protected promotion hard-reject non-`main` refs.

The workflow artifact is intentionally sanitized. It contains the commit,
image digest, Central primary revision, East rollback revision, their verified
regions, the public DNS target, Alembic head, job execution IDs/statuses, bounded
request IDs, aggregate dispatcher sample/p50/p95/max and per-handler timing,
parent-browser CORS probe counts, callback pass/hash facts,
alert-test acceptance, and pass/fail facts. It must never contain tokens, OAuth
authorization codes or state, callback query strings, SAS URLs, emails, join
codes, child/user text, coordinates, or images.
Receiving the action-group test is still a human evidence item; API acceptance
alone does not prove that an operator saw it.

Before the adult dry run is accepted, use a fresh browser context and complete
the real sequence exactly once: open `/consent`, record consent, continue to
Entra, return through `/auth/callback`, confirm the app obtains the canonical
`/v1/me`, and land in `/classroom`. A 404 or redirect away from the callback,
an unhandled login result, a failed `/v1/me`, or failure to reach the classroom
is a hard stop. Do not save a HAR, full callback URL, browser-history export, or
screenshot containing the callback query; retain only sanitized pass/fail
facts and bounded request IDs.

## Monitoring contract

Provision or audit the same alerts outside promotion with:

```bash
HINTERLAND_ALERT_EMAIL="ops@example.invalid" \
bash infra-azure/observation-w1-monitoring.sh --apply --verify --synthetic
```

The artifact covers moderation and pending-photo age, moderation queue/DLQ,
rebuild backlog/terminal failure, dispatcher backlog/p95, idempotency conflicts,
state mismatches, missing/failed scheduled jobs, and stale or terminal
photo-revocation failure. The photo-revocation recovery job is a closed-beta resource and is not
required in the W1 Azure inventory until it is separately provisioned.

Azure accepts action-group test notifications asynchronously. The promotion
records that its explicit, enabled protected receiver accepted the request; the
adult must separately record actual email receipt before declaring W1-ready.

## Exact Play Internal evidence

Server promotion is necessary but not sufficient. Build with
`APP_ENV=play-internal`, record EAS build ID, version code, AAB SHA-256, package,
display name, update channel, and manifest permissions, then upload the exact
AAB to Play Internal. Install from the Play opt-in link on a physical Android
device with at most 4 GB RAM. The required Maestro assertion is a real saved
Journal card in the metadata-only private state with no photo request—not only
that the Field Journal screen exists.

Also perform airplane capture, kill after PUT/lost-create-response, picker
recovery, account switch, location denial/imported-photo, catalog/manual/Unknown,
and private-photo non-rendering tests. Record device/Android details and request
IDs, but no child photo, text, or coordinates.

Any code, configuration, or content change after the promoted commit or AAB is
built invalidates that AAB's W1 evidence. Land the repair through a reviewed PR,
rerun the protected server promotion, build and publish a new versioned AAB,
and repeat the required physical-device runs. Never reuse an older AAB merely
because its package name or server API is unchanged.

## Fresh-package cutover

`app.thehinterlandguide` is a fresh Android sandbox. Do not attempt to copy
SQLite or SecureStore data from an older package.

Before retiring each old install, use the adult-supervised old app to inventory
owner-scoped queued submissions. Keep the old app installed until every item
has either reconciled to a canonical server Observation or the adult has
explicitly chosen discard. Record the queue count and outcome without photos,
text, or coordinates. If there are no old installs, record `zero old installs`
instead of silently skipping the gate. Only then uninstall/retire the old app
and perform a fresh kid handoff in `app.thehinterlandguide`.

## Stop and rollback

For unauthorized photo access, wrong-account data, raw coordinate leakage,
duplicate/lost retry work, or any pre-clean/external photo egress, stop testing
and preserve evidence. Restore the W1 flags, stop the affected Container Apps
job, and revoke its queue access. A code rollback uses a known-good Azure
Container Apps revision and must never restore Event Grid moderation, CV, or
iNaturalist submission. Follow `android-internal-pilot-stop-plan.md`; there is
no Cloud Run rollback path.

For a Central API readiness, TLS, authentication, privacy, or p95 failure,
leave PostgreSQL and every job unchanged and restore the public `api` DNS
record to the retained East API target. Both API apps must already be running
the same immutable digest before a DNS rollback. Keep the Central evidence and
request IDs, verify public readiness/JWKS after propagation, and do not delete
either environment during the W1 evidence window.

A missing trusted parent origin is a promotion blocker. A wildcard or an
unexpected origin receiving `Access-Control-Allow-Origin` is a security hard
stop: preserve the preflight evidence and roll forward or back to an exact
allowlist before any further consent or child testing.
