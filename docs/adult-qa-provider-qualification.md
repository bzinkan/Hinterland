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
acceptable project-catalog taxon IDs for each label.

Use a fresh QA kid-session bearer. It is distinct from the iNaturalist CV
credential and is supplied only through process memory:

```powershell
$env:HINTERLAND_QA_BENCHMARK_BEARER = Read-Host -AsSecureString |
  ConvertFrom-SecureString -AsPlainText
python scripts/qa_inat_cv_benchmark.py `
  --manifest C:\private\qa-cv-manifest.json `
  --output C:\Users\zinka\AppData\Local\Hinterland\qa-evidence\<sha>\cv-benchmark.json
Remove-Item Env:\HINTERLAND_QA_BENCHMARK_BEARER
```

The runner refuses non-QA API origins, fewer or more than 50 cases, a bearer
with less than 30 minutes remaining, a wrong/missing release-lane header,
disabled Content Safety/photo-helper capability, or enabled public
iNaturalist submission. Calls are paced at least one second apart. Passing
requires at least 70% top-three correctness and zero unavailable or malformed
responses.

The output contains only aggregate counts and timestamps. It excludes tokens,
photo bytes, labels, observation IDs, names, locations, and provider response
payloads.
