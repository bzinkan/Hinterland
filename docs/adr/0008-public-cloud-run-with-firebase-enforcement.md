# ADR 0008: Public Cloud Run with Firebase enforcement

- **Status:** Accepted
- **Date:** 2026-05-07
- **Deciders:** Solo author
- **Related:** ADR 0005 (GCP target architecture), ADR 0007 (Multi-agent AI is internal-only)

## Context

The `hinterland-app.net` Workspace organization enforces the
`constraints/iam.allowedPolicyMemberDomains` org policy. With it in place,
attempts to grant `allUsers` or `allAuthenticatedUsers` the
`roles/run.invoker` role are silently rejected — Cloud Run accepts the
binding API call but the policy filters it out. We've hit this every time
we tried `gcloud run deploy --allow-unauthenticated`.

This is fine for service-to-service or human-via-Google-identity callers
(both are domain-bound), but it blocks the closed-beta mobile path: an
Expo client running on a kid's iOS or Android device has no Google
identity it can present to Cloud Run's IAM check. The mobile client
authenticates with **Firebase Auth** and presents a Firebase ID token in
the `Authorization` header. Our FastAPI app verifies that token via the
Firebase JWKS in `app/core/auth.py` (per ADR 0005, wired up in PR #8).

So the IAM check at Cloud Run is doing nothing for us — every request is
a stranger from the network's perspective and the real auth boundary is
in our app code. Leaving `allUsers` blocked means we either:

- run mobile clients through an additional service (Cloud Endpoints, API
  Gateway, or IAP) that re-issues Google-identity tokens, or
- relax the constraint at project scope and let `allUsers` invoke the
  Cloud Run service directly while Firebase enforces auth in-app.

## Decision

Override `iam.allowedPolicyMemberDomains` at project scope for the **dev
project (`hinterlandapp-495423`)**. Combine with an explicit `allUsers`
invoker binding on the `hinterland-api` Cloud Run service. Authentication
becomes the responsibility of `app/core/auth.py` and the Firebase ID
token verification it performs.

The override is opt-in via a Terraform variable
`cloud_run_public` (default `false`). Dev sets it to `true`; staging and
prod inherit the org policy default and stay restrictive until a
follow-up ADR per environment.

Apply ordering is enforced by `depends_on`: the org policy override
applies before the `allUsers` invoker binding.

## Consequences

### Positive

- **Mobile clients can call the API directly.** No proxy layer, no
  Cloud-identity gymnastics. Mobile sends a Firebase ID token in
  `Authorization: Bearer ...`, our app verifies it, every other endpoint
  enforces role + group_id from the token's custom claims.
- **The `/health` smoke test stops needing a Google identity token.**
  External monitoring services (UptimeRobot, BetterUptime, Cloud
  Scheduler with public auth, etc.) can hit it without a service-account
  workaround.
- **One auth boundary instead of two.** Firebase Auth is now the only
  thing the team has to reason about for "who is this request from."

### Negative

- **The dev project loses domain-restricted-sharing protection.** Any
  IAM binding accidentally added with `allUsers` or
  `allAuthenticatedUsers` (on Cloud Storage, Secret Manager, Cloud SQL,
  etc.) will now succeed silently — without the org policy as a backstop.
  Mitigation: PRs that add IAM bindings get a review pass for principal
  scope; staging/prod keep the constraint until an explicit per-env ADR
  loosens it.
- **Apply requires elevated permissions.** `google_org_policy_policy`
  needs `roles/orgpolicy.policyAdmin` at the project. The
  `github-deploy-dev` service account does not have this role, so the
  first `terraform apply` of this resource must be run by a human with
  the role (the project Owner has it implicitly). Subsequent applies
  that don't touch the policy succeed via the SA's existing roles. If
  CI-driven apply of org policy changes is ever needed, grant the role
  explicitly in a follow-up.
- **No defense-in-depth at the IAM layer.** A bug in
  `app/core/auth.py` — unverified token, missing dependency, accidentally
  unauthenticated route — exposes the API to the public internet. Without
  the IAM gate, Firebase verification is the only thing standing between
  a kid's photo-upload endpoint and a stranger.
  Mitigation: integration tests at the API boundary that POST without an
  auth header and assert 401, plus middleware that enforces auth on every
  route except `/health`, `/ready`, and `/v1/meta`.

### Neutral

- **Port the policy decision to staging/prod separately.** When those
  environments come online with mobile traffic, this same decision will
  need a per-env ADR. Most likely the answer will be the same shape but
  with a narrower allow-list (e.g., `hinterland-app.net` + a CDN range)
  rather than `allow_all`.

## Alternatives considered

### Cloud Endpoints / API Gateway in front of Cloud Run

**Rejected for now.** Cloud Endpoints can authenticate Firebase ID
tokens and forward to Cloud Run with a service-account identity, keeping
Cloud Run private. Cloud Endpoints adds its own deploy surface, OpenAPI
spec, dev portal, quotas, and pricing. The features we'd actually use
(Firebase auth verification) are already in our FastAPI app. Bringing
Endpoints in just to satisfy the org policy is a lot of moving parts for
no product benefit.

Reconsider if we ever want quota management, API keys, or a developer
portal — those are real Endpoints wins.

### Identity-Aware Proxy

**Rejected.** IAP authenticates users via OAuth, which is correct for
human callers (web admins, dashboards) but awkward for mobile apps that
already have a Firebase identity flow. Wrong tool for the call site.

### Per-Cloud-Run-service IAM condition allowing `allUsers`

**Rejected.** Conditional IAM bindings (with CEL `request.path` filters,
say) are technically possible but create a more complex policy that
breaks the simple Firebase-Auth-is-the-only-boundary mental model. Too
clever.

### Switch from `allow_all = "TRUE"` to an explicit allowed list

**Considered, deferred.** A narrower override (e.g., allow only
`allUsers` and `domain:hinterland-app.net`, leaving
`allAuthenticatedUsers` blocked) reduces the blast radius if a future
mistake binds something to `allAuthenticatedUsers`. Not worth the
complexity in dev where convenience matters more; revisit when mapping
the staging/prod policy. This ADR explicitly defers that to a future
per-env ADR.

## Follow-ups

- Verify after apply: `gcloud run services get-iam-policy hinterland-api
  --region us-central1` should show `allUsers` with `roles/run.invoker`.
  Then a fresh-tab `curl` to `https://api.hinterland-app.net/health`
  (no auth header) should return 200.
- Update `docs/runbook.md` "Smoke-testing `/health` in dev" section to
  drop the identity-token requirement once the change is applied.
- Add a middleware-level "auth required" enforcement test to
  `backend/tests/` so an unauthenticated request to a non-public route
  returns 401 — defense in depth against an accidentally-unauthenticated
  handler.
- Decide staging/prod policy in their own ADRs (0009, 0010) when each
  environment comes online with mobile traffic. Default to the
  narrowest workable allow-list, not `allow_all`.
- If CI-driven apply of org policy changes ever becomes desirable,
  grant `roles/orgpolicy.policyAdmin` (project scope) to
  `github-deploy-dev` in Terraform — and document the privilege
  escalation in a separate ADR.
