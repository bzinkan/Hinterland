# Taxonomy catalog packs

These reviewed JSON packs are the project-owned runtime taxonomy source. Kid
requests search PostgreSQL or a verified mobile pack; they never fall through
to a live iNaturalist taxon lookup.

`manifest.json` pins each immutable pack's version, byte length, SHA-256, and
taxon count. Run:

```powershell
backend/.venv/Scripts/python.exe scripts/validate_taxonomy.py
```

The core pack contains every higher/iconic taxon, direct taxon ID, and taxon-set
member referenced by checked-in Expedition content, plus reviewed common
starter species. Integer IDs remain iNaturalist taxon identifiers under ADR
0015. Taxon IDs and canonical names must be verified before a pack version is
bumped.

Publish a pack with the audited ingest job (managed identity uploads the
immutable Blob and records the manifest row):

```powershell
cd backend
.venv/Scripts/python.exe -m admin.taxa_catalog_ingest ../content/taxa/core.json
```

Use `--database-only` only for local search development; it deliberately leaves
the downloadable manifest inactive.
