# Risk 0004: Expedition authoring tool was a stub

- **Status:** Resolved
- **Date filed:** 2026-05-10
- **Resolved:** 2026-06-04
- **Owner:** Brian

## Resolution

`scripts/draft_expedition.py` now generates a schema-valid expedition JSON
scaffold from a short prompt:

```bash
python scripts/draft_expedition.py "city park insects" --environment park
```

It is intentionally author-time only:

- no backend import path
- no API route
- no network call
- no agent framework
- no kid-facing runtime LLM

The generated draft is validated through `Expedition.model_validate` before it
is printed or written. Authors still review/edit the JSON, run
`python scripts/validate_content.py`, and commit the final content.

## Future Enhancement

A provider-backed LLM drafting mode can be added later if Brian explicitly
chooses a provider and budget. That would be a convenience feature, not a beta
blocker.

**Update 2026-07-02:** the drafting mode landed as
`--provider anthropic` on `scripts/draft_expedition.py`. It stays author-time
only per ADR 0002: the model is pinned (`ANTHROPIC_MODEL` in the script), the
`anthropic` SDK is lazily imported and deliberately not a backend dependency,
output is validated through `Expedition.model_validate` (one retry, then a
static-template fallback), and a human reviews every draft. The deterministic
static template remains the default provider.
