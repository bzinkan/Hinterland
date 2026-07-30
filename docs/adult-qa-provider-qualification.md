# Adult QA provider qualification

Provider qualification is permitted only in the isolated `adult_qa` release
lane. W1 remains NoOp and must not receive Content Safety or iNaturalist
credentials.

## Disclosure boundary

The owning adult must hold linked disclosure
`2026-07-30-ADULT-QA-EGRESS-V1`. It explains that:

- Azure Content Safety receives the canonical QA photo;
- iNaturalist CV receives a clean/adult-approved photo only when the tester
  requests suggestions;
- public iNaturalist publication remains disabled; and
- testers use synthetic child profiles and adult-owned, non-sensitive photos.

The moderation worker and identify route check this exact receipt before
reading photo bytes for provider egress.

## 50-photo CV benchmark

Prepare 50 adult-owned labeled photos as clean or adult-approved QA
observations. Copy `scripts/qa-inat-cv-benchmark.example.json` to a private
operator location, add exactly 50 distinct observation ULIDs, and list the
acceptable project-catalog taxon IDs for each label. Every entry also names
its server-verified canonical photo SHA-256; all 50 hashes must be distinct.

Use a fresh QA kid-session bearer. It is distinct from the iNaturalist CV
credential and is supplied only through process memory. Before the benchmark,
promote Adult QA in bounded CV qualification mode: real Content Safety, the
current disclosure, the CV token, and the API-only qualification grant are
present, while benchmark approval and the kid-facing photo-helper capability
remain false. The grant contains a fresh run ULID, the exact manifest SHA-256,
an expiry no more than 24 hours away, and a JSON object mapping the exact 50
observation ULIDs to their canonical photo hashes. Its separate high-entropy
key is held in QA Key Vault and is never embedded in the app.

```powershell
$env:HINTERLAND_QA_BENCHMARK_BEARER = Read-Host -AsSecureString |
  ConvertFrom-SecureString -AsPlainText
$env:HINTERLAND_QA_CV_QUALIFICATION_KEY = Read-Host -AsSecureString |
  ConvertFrom-SecureString -AsPlainText
python scripts/qa_inat_cv_benchmark.py `
  --manifest C:\private\qa-cv-manifest.json `
  --output C:\Users\zinka\AppData\Local\Hinterland\qa-evidence\<sha>\cv-benchmark.json
Remove-Item Env:\HINTERLAND_QA_BENCHMARK_BEARER
Remove-Item Env:\HINTERLAND_QA_CV_QUALIFICATION_KEY
```

The runner refuses non-QA API origins, fewer or more than 50 cases, a bearer
with less than 30 minutes remaining, a wrong/missing release-lane header,
disabled Content Safety/qualification capability, an already-enabled
kid-facing photo helper, or enabled public iNaturalist submission. Calls are
paced at least one second apart. The server locks each photo against
revocation, rejects cases outside the exact allowlist, and stores results
under a run-scoped cache key. A cached/replayed response is explicitly marked
and fails the benchmark: passing requires 50 fresh provider calls, 50 distinct
canonical hashes, at least 70% top-three correctness, and zero unavailable or
malformed responses.

After the aggregate result passes, disable qualification mode and remove the
qualification key from the API. Record benchmark approval, promote again, and
then verify `photo_helper_enabled=true`. Qualification mode and benchmark
approval are mutually exclusive, so the API fails startup if both are set.

The output contains aggregate counts plus the API revision, immutable image
digest, CV model version, run/manifest identifiers, dataset-hash attestation,
and catalog attestations. It excludes tokens, photo bytes, labels,
observation IDs, names, locations, and provider response payloads.
