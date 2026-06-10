# Dragonfly Landing Deploy Runbook

This runbook verifies that the public Dragonfly landing surface is deployable,
live at `https://dragonfly-app.net`, and suitable for Google Play Console,
parent, guardian, and teacher review once legal copy is finalized.

The public pages are static HTML/CSS. Do not add analytics, ad SDKs, tracking
pixels, chat widgets, Play service-account credentials, or secrets to this
surface.

## Current deploy path

| Item | Current value |
|---|---|
| Source | `web/public/` |
| Local static check | `cd web && npm run check` |
| Live smoke check | `cd web && npm run smoke:live` |
| Active public domain | `https://dragonfly-app.net/` and `https://www.dragonfly-app.net/` |
| Public hosting provider for apex + www | Firebase Hosting site `dragonfly-landing-dev` |
| Firebase workflow | `.github/workflows/deploy-landing-dev.yml` |
| Firebase target | `web/.firebaserc` target `www` -> site `dragonfly-landing-dev` |
| Firebase project | `dragonflyapp-495423` |
| Firebase required secrets | `GCP_WORKLOAD_IDENTITY_PROVIDER`, `GCP_SERVICE_ACCOUNT` |
| Azure standby/parallel host | Azure Static Web Apps site `dragonfly-landing-swa` |
| Azure workflow | `.github/workflows/deploy-landing-swa.yml` |
| Azure required secret | `AZURE_LANDING_SWA_TOKEN` |
| GitHub token | `GITHUB_TOKEN` is supplied automatically by GitHub Actions |

Both landing workflows run on:

- `push` to `main`
- `workflow_dispatch`

Both workflows are path-filtered to landing changes:

- `web/**`
- their own workflow file

The parents web deploy is separate. The Firebase workflow
`.github/workflows/deploy-web-dev.yml` and Azure workflow
`.github/workflows/deploy-parents-swa.yml` watch `mobile/**` and must not be
changed for landing-only work.

## Hosting and DNS reality

ADR 0010 selected Azure as the target architecture, and
`dragonfly-landing-swa` exists as the Azure Static Web Apps landing host.
However, the current stable public-domain decision is:

- `dragonfly-app.net` and `www.dragonfly-app.net` stay on Firebase Hosting.
- Cloud DNS zone `dragonfly-app-zone` remains authoritative.
- Azure SWA deploys continue in parallel as a migration/standby path.

The reason is documented in `infra-azure/phase-10-gcp-decommission.md`:
Azure Static Web Apps apex-domain support is cleanest when Azure DNS is the
authoritative nameserver. Moving the whole zone just to serve the static landing
page would add registrar-side risk without functional payoff.

If a future PR moves apex/www to Azure SWA, it must update this runbook,
`docs/landing-page.md`, and the relevant infra/deploy notes in the same PR.

## Public URLs

These URLs must return HTTP 200 and be suitable for Google Play Console fields
once the privacy and terms copy has legal review:

- `https://dragonfly-app.net/`
- `https://dragonfly-app.net/privacy`
- `https://dragonfly-app.net/terms`
- `https://dragonfly-app.net/support`
- `https://dragonfly-app.net/contact`

## After-merge deploy verification

After a landing PR is merged to `main`, verify the GitHub Actions runs for the
merge commit:

```sh
gh run list --branch main --limit 10 \
  --json databaseId,workflowName,displayTitle,status,conclusion,headSha,url
```

Expected workflows for landing changes:

- `Deploy landing site (dev)` -> Firebase Hosting
- `Deploy landing site to Azure Static Web Apps (dev)` -> Azure SWA
- `CI`

Watch a run if needed:

```sh
gh run watch <run-id> --interval 10
```

Non-blocking GitHub Actions annotations about Node action runtime deprecations
or cache restore/save warnings do not block landing deploy if the jobs conclude
`success`. Fix them in a separate workflow-maintenance PR if they become noisy
or start failing jobs.

## Manual public smoke

Open each page in a browser:

```sh
start https://dragonfly-app.net/
start https://dragonfly-app.net/privacy
start https://dragonfly-app.net/terms
start https://dragonfly-app.net/support
start https://dragonfly-app.net/contact
```

On macOS/Linux, replace `start` with `open` or `xdg-open`.

Check HTTP 200 for all public pages:

```sh
curl -I https://dragonfly-app.net/
curl -I https://dragonfly-app.net/privacy
curl -I https://dragonfly-app.net/terms
curl -I https://dragonfly-app.net/support
curl -I https://dragonfly-app.net/contact
```

Check title/meta on the homepage:

```sh
curl -fsS https://dragonfly-app.net/ | grep -E "<title>|description|og:title|twitter:card"
```

Check visible-page placeholders. Public pages should not expose `TODO`,
`FIXME`, or `PLACEHOLDER` unless the copy intentionally says a page is a pilot
draft:

```sh
for path in / /privacy /terms /support /contact; do
  curl -fsS "https://dragonfly-app.net${path}" \
    | grep -Eio "TODO|FIXME|PLACEHOLDER" \
    && echo "placeholder found on ${path}"
done
```

Check the pilot CTA opens email:

1. Open `https://dragonfly-app.net/`.
2. Activate `Request pilot access`.
3. Confirm the link opens an email to `support@dragonfly-app.net`.
4. Confirm the subject is `Dragonfly pilot access request`.
5. Confirm the body asks for adult contact/logistics only and includes the
   instruction not to include a child's full name.

Check mobile viewport:

1. Open Chrome or Edge DevTools.
2. Toggle device toolbar.
3. Verify widths `360`, `390`, `768`, and desktop.
4. Confirm no horizontal scrolling, clipped header CTA, clipped hero copy,
   unreadably narrow cards, or wrapped footer links hiding support/privacy
   email addresses.

## Automated live smoke

Run the no-dependency live smoke from `web/`:

```sh
cd web
npm run smoke:live
```

Optional custom domain:

```sh
LANDING_BASE_URL=https://www.dragonfly-app.net npm run smoke:live
```

Local static preview:

```sh
python -m http.server 4175 --bind 127.0.0.1 --directory web/public
cd web
LANDING_BASE_URL=http://127.0.0.1:4175 npm run smoke:live
```

The local preview path falls back from `/privacy` to `/privacy.html` because
Python's static server does not emulate Firebase/SWA clean URLs.

The script checks:

- HTTP 200 for `/`, `/privacy`, `/terms`, `/support`, and `/contact`
- homepage title/meta/JSON-LD markers
- canonical URLs on the legal/support/contact pages
- no visible `TODO`, `FIXME`, or `PLACEHOLDER`
- pilot CTA `mailto:` subject/body guardrails
- support and privacy email addresses

The script does not replace a human mobile viewport pass. Use the browser steps
above before relying on the public site for a Play review or parent pilot link.

## Remaining manual gates

No DNS or hosting changes are required for ordinary landing-page copy/CSS
changes. The existing Firebase Hosting path serves the public apex and `www`
domains, and the Azure SWA deploy remains a parallel standby path.

Before using the URLs for Closed/Open/Production store tracks:

- privacy and terms copy must be lawyer-reviewed
- `support@dragonfly-app.net` must be monitored
- `privacy@dragonfly-app.net` must be monitored
- Google Play data-safety and target-audience answers must be re-verified
- any future Azure DNS/SWA cutover must be smoke-tested from this runbook
