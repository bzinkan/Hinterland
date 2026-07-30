# Adult QA mobile release

Adult QA is a separate Google Play Internal application. It does not replace,
update, or share an application sandbox with the W1 Internal build.

## Identity

| Setting | Adult QA value |
|---|---|
| Expo environment | `adult-qa` |
| Package / bundle | `app.thehinterlandguide.qa` |
| Display name | `The Hinterland Guide QA` |
| Scheme | `hinterland-qa` |
| EAS slug | `hinterland-qa` |
| EAS channel | `adult-qa` |
| API | `https://qa-api.thehinterlandguide.app` |
| Parent web | `https://qa-parents.thehinterlandguide.app` |
| Expected API lane | `adult_qa` |

The build requires these public identifiers from the isolated QA
provisioning output:

- `HINTERLAND_QA_EAS_PROJECT_ID`
- `HINTERLAND_QA_ENTRA_CLIENT_ID`
- `HINTERLAND_QA_ENTRA_TENANT_ID`
- `HINTERLAND_QA_ENTRA_API_AUDIENCE`

They are identifiers, not credentials. No development-login key is embedded.

The QA parent web records disclosure version
`2026-07-30-ADULT-QA-EGRESS-V1`. It states that Azure Content Safety receives
canonical QA photos, iNaturalist CV receives only clean/adult-approved photos
after a tester asks for suggestions, public publication is disabled, and only
adult-owned non-sensitive photos with synthetic child profiles are permitted.
The API and moderation worker require that exact linked receipt before
provider egress.

## Fail-closed release handshake

Before starting authentication, queues, or normal API requests, the app calls
`GET /v1/meta`. Both the JSON `release_lane` and the
`X-Hinterland-Release-Lane` response header must equal `adult_qa`. A missing,
malformed, unavailable, or mismatched response leaves the app on a blocking
safety screen. Every later API response must carry the same header.

This is a deployment-isolation control, not an authorization substitute.

## Build checks

From `mobile/`:

```powershell
npm ci
npm run typecheck
npm test -- --runInBand
npm run config:identities
npm run config:play-internal
npm run config:adult-qa
npx eas-cli build --platform android --profile adult-qa --non-interactive
```

The QA config check enforces the separate EAS project, package, scheme,
domains, embedded update bundle, coarse-only location, blocked microphone and
broad-storage permissions, stable Sanctuary 2.5D flag, and absence of a
development-login key.

The exact AAB must be uploaded to the separate Adult QA Play Internal app.
Never sideload it for release evidence, and never upload it to the W1,
Closed, Open, or Production tracks.
