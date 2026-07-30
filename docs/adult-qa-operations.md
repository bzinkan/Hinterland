# Adult QA infrastructure operations

Adult QA is a separate, adult-only test lane. It does not change the W1
environment or authorize child testing. Use synthetic child profiles and
adult-owned, non-sensitive photos only.

The source-of-truth contract is
[`infra-azure/environments/hinterland-qa.env`](../infra-azure/environments/hinterland-qa.env).
The isolation decision is [ADR 0018](adr/0018-isolated-adult-qa-environment.md).

## 1. Cost preview and preflight

This command is read-only:

```powershell
pwsh -NoProfile -File infra-azure/adult-qa-preflight.ps1 `
  -Mode Preview `
  -OutputPath C:\Users\zinka\AppData\Local\Hinterland\qa-evidence\cost-preview.json
```

The estimate is deliberately conservative and is not an Azure quote. Confirm
current regional pricing in Azure Pricing Calculator and Cost Management.

No provisioning tool may run until an operator reviews that output and runs the
same preflight in approval mode:

```powershell
pwsh -NoProfile -File infra-azure/adult-qa-preflight.ps1 `
  -Mode ProvisionReadiness `
  -Confirmation APPROVE_HINTERLAND_ADULT_QA_COSTS `
  -OutputPath C:\Users\zinka\AppData\Local\Hinterland\qa-evidence\provision-approval.json
```

This second command is also read-only. It creates a sanitized approval record;
it does not create Azure resources. A future reviewed provisioning artifact
must validate and retain that record before its first write.

Both modes fail if the contract points at `hinterland-dev-rg`,
`gordi-pilot-rg`, the W1 database/role, W1 Key Vault, W1 storage, W1
identities, W1 Service Bus, or W1 queues. Only ACR, Log Analytics, and the
PostgreSQL server are approved shared services.

## 2. Protected promotion setup

Create a GitHub environment named `adult-qa-promotion`:

- restrict deployments to `main`;
- require an adult reviewer;
- retain repository-scoped Azure OIDC credentials; and
- set the non-secret environment variable
  `HINTERLAND_QA_COST_APPROVAL_ID` to the retained approval record ID.

Add environment secrets without copying values into issues, logs, or chat:

- `HINTERLAND_QA_PARENTS_SWA_TOKEN`;
- `HINTERLAND_QA_SMOKE_ENTRA_BEARER`; and
- `HINTERLAND_QA_ALERT_EMAIL` when the monitoring verifier requires it.

The test-parent bearer must be issued to the dedicated QA client and have at
least 30 minutes remaining at dispatch. The workflow validates issuer, tenant,
audience, authorized client, `user.access`, object identity, and expiry without
printing the token. It does not skip the authenticated parent → synthetic kid
→ clean Observation canary.

The workflow does not provision missing resources. It verifies the exact QA
contract, runs migrations before workers/API, deploys one digest, runs the
authenticated canary, and keeps public iNaturalist submission disabled.

Dispatch from `main`:

```powershell
gh workflow run adult-qa-promotion.yml `
  --repo bzinkan/Hinterland `
  --ref main `
  -f confirmation=PROMOTE_ADULT_QA `
  -f enable_cv=false `
  -f cv_disclosure_approved=false `
  -f cv_benchmark_approved=false `
  -f shared_groups_enabled=false
```

CV can become true only in a later protected run where both approvals are true
and the unexpired QA token secret exists. That run must also supply the exact
approved qualification run ULID and aggregate evidence SHA-256, matching the
protected `HINTERLAND_QA_CV_APPROVED_RUN_ID` and
`HINTERLAND_QA_CV_APPROVED_EVIDENCE_SHA256` environment variables. Public
submit has no input and always remains false.

## 3. Rotate the QA-only iNaturalist CV token

The retired refresh script targeted W1 and rolled a live app. It has been
removed. The supported operation can target only
`hinterland-qa-rg/hinterland-kv-qa`, accepts only the `inat-cv-token` secret,
and does not roll or enable anything.

First sign in to the exact Azure subscription, then run:

```powershell
pwsh -NoProfile -File scripts/set-adult-qa-inat-cv-token.ps1 `
  -MetadataOutputPath C:\Users\zinka\AppData\Local\Hinterland\qa-evidence\inat-cv-expiry.json
```

Paste either the raw short-lived JWT or the `{"api_token":"..."}` response at
the hidden prompt. The operation:

1. verifies the subscription, tenant, QA resource group, QA vault, and QA tags;
2. requires 30 minutes to 48 hours of remaining token lifetime;
3. sends the token directly from memory to Key Vault's data plane;
4. tags the secret as CV-only with public submit disabled; and
5. prints/writes only rotation and expiry metadata.

It does not use a clipboard, temp file, process argument, shell history,
repository file, or W1 resource. Do not use `StandardInput` mode from an
interactive terminal; it exists only for a secure caller that redirects its
own in-memory value.

Possessing the token is not consent and does not enable egress. The disclosure,
benchmark, real-moderation, clean-photo, revocation, and protected-promotion
gates remain independent.

## 4. Run the bounded CV qualification

Qualification is a separate protected workflow, not a promotion flag. Prepare
exactly 50 already-clean/adult-approved observations owned by the synthetic QA
profile. The private manifest follows
[`scripts/qa-inat-cv-benchmark.example.json`](../scripts/qa-inat-cv-benchmark.example.json).
It contains canonical observation ULIDs, verified photo hashes, and reviewed
accepted catalog taxa; it is never committed or uploaded as evidence.

Set these short-lived `adult-qa-promotion` environment secrets directly:

- `HINTERLAND_QA_CV_BENCHMARK_MANIFEST_B64`: base64 of the private manifest;
- `HINTERLAND_QA_BENCHMARK_BEARER`: a synthetic QA kid bearer with at least
  30 minutes remaining.

The workflow generates a fresh high-entropy request key in memory. It stores
that key under a run-unique QA Key Vault secret, maps it only into the
qualification revision, and deletes the active Key Vault secret and runtime
mapping during cleanup. The key is never supplied by an operator or retained
as a GitHub secret.

Dispatch `adult-qa-cv-qualification.yml` with a fresh run ULID, the exact
manifest SHA-256, an expiry 30 minutes–24 hours away, and the confirmation
`QUALIFY_ADULT_QA_CV`. The workflow:

1. verifies the current QA API is the exact `main` commit and digest;
2. derives the server allowlist from the protected 50-case manifest;
3. enables qualification while the normal kid photo-helper remains disabled;
4. requires 50 distinct canonical hashes and 50 real provider calls;
5. requires at least 70% top-three accuracy with zero unavailable/malformed
   cases;
6. archives aggregate evidence only; and
7. always disables qualification, removes its runtime variables and secret
   mappings, and destroys the runner’s private manifest copy.

After reviewing a passing aggregate, record its run ULID and evidence SHA-256
as the two protected approval variables named above. Delete the two
short-lived GitHub environment secrets. The provider token remains in the QA
Key Vault until its natural expiry, but no API or job receives it until a later
approved promotion explicitly enables CV.

## 5. Evidence and stop controls

Promotion artifacts may include commit, digest, revisions, Alembic head, job
execution IDs/statuses, queue/DLQ counts, alert names, lifecycle rule names,
request IDs, and pass/fail facts. They must never include:

- access or refresh tokens;
- secret values;
- SAS URLs;
- image bytes or hashes that identify tester content;
- child/manual text;
- raw or precise coordinates; or
- QR/callback payloads.

To stop QA, disable the QA moderation relay and CV, stop QA scheduled jobs, and
scale the QA API down. Do not delete or reconfigure W1 resources. A stop does
not authorize deleting retained test data; follow the QA lifecycle and
revocation procedures.
