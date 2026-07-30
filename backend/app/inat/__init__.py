"""iNaturalist API integration.

Two surfaces:

- `client.py` -- separate public, CV, and submit async httpx clients.
- `cv.py` -- /v1/computervision/score_image (kid-photo species suggestions).
- `taxa.py` -- /v1/taxa/{id} (taxon lookup for the species cache).

CV and public submission use separate credentials. If the dedicated CV token
is absent, malformed, expired, or near expiry, the CV path returns a
`cv_unavailable` shape rather than raising; callers fall back to manual species selection per
`docs/architecture.md` external-dependency table.
"""
