# ADR 0013: Final Hinterland Identity

- **Status:** Accepted
- **Date:** 2026-07-09
- **Deciders:** Solo author
- **Related:** ADR 0010, ADR 0014

## Decision

The product name is **The Hinterland Guide**. **Hinterland** is the compact
name for technical and space-constrained surfaces.

All active repository content, runtime configuration, mobile metadata, public
web copy, and Azure resources use Hinterland naming. The repository naming gate
rejects the retired identifier in tracked filenames and file content.

The active contracts are:

- Mobile packages: `app.thehinterlandguide`,
  `app.thehinterlandguide.staging`, and `app.thehinterlandguide.dev`
- Expo slug and owner: `the-hinterland-guide` and `thehinterlandguide`
- Deep-link scheme: `hinterland`
- Settings prefix: `HINTERLAND_`
- Kid QR payload: `hinterland.kid-handoff.v1`
- Kid JWKS: `/.well-known/hinterland-kid-jwks.json`

This is a clean cutover. The current service does not retain aliases for old
configuration, QR payloads, package identities, or public routes.

## Consequences

- Existing mobile installs are replaced by fresh Hinterland builds.
- The active EAS project and Google Play Internal Testing listing are new
Hinterland properties.
- A retired environment may be deleted only after the Azure replacement passes
acceptance checks and its encrypted backup completes the required 30-day
retention window.
- Git history remains the sole historical record of the previous identity.
