# Hinterland Landing Deploy Runbook

This runbook verifies that the public landing/legal surface is deployable,
live at `https://thehinterlandguide.app`, and suitable for Google Play Console,
parent, guardian, and teacher review once legal copy is finalized.

The public pages are static HTML/CSS. Do not add analytics, ad SDKs, tracking
pixels, chat widgets, Play service-account credentials, or secrets to this
surface.

## Current Deploy Path

| Item | Current value |
|---|---|
| Source | `web/public/` |
| Local static check | `cd web && npm run check` |
| Live smoke check | `cd web && npm run smoke:live` |
| Active public domain | `https://thehinterlandguide.app/` and `https://www.thehinterlandguide.app/` |
| Hosting provider | Azure Static Web Apps site `hinterland-landing-swa` |
| Workflow | `.github/workflows/deploy-landing-swa.yml` |
| Required secret | `AZURE_LANDING_SWA_TOKEN` |
| GitHub token | `GITHUB_TOKEN` is supplied automatically by GitHub Actions |

The parents web deploy is separate. `.github/workflows/deploy-parents-swa.yml`
watches `mobile/**` and deploys the Expo web bundle to
`hinterland-parents-swa`.

## Public URLs

These URLs must return HTTP 200 and be suitable for Google Play Console fields
once the privacy and terms copy has legal review:

- `https://thehinterlandguide.app/`
- `https://www.thehinterlandguide.app/`
- `https://thehinterlandguide.app/privacy`
- `https://thehinterlandguide.app/terms`
- `https://thehinterlandguide.app/support`
- `https://thehinterlandguide.app/contact`

## After-Merge Deploy Verification

After a landing PR is merged to `main`, verify the GitHub Actions runs for the
merge commit:

```sh
gh run list --branch main --limit 10 \
  --json databaseId,workflowName,displayTitle,status,conclusion,headSha,url
```

Expected workflows for landing changes:

- `Deploy landing site to Azure Static Web Apps (dev)`
- `CI`

Watch a run if needed:

```sh
gh run watch <run-id> --interval 10
```

## Manual Public Smoke

Open each page in a browser:

```sh
start https://thehinterlandguide.app/
start https://thehinterlandguide.app/privacy
start https://thehinterlandguide.app/terms
start https://thehinterlandguide.app/support
start https://thehinterlandguide.app/contact
```

On macOS/Linux, replace `start` with `open` or `xdg-open`.

Check HTTP 200 for all public pages:

```sh
curl -I https://thehinterlandguide.app/
curl -I https://www.thehinterlandguide.app/
curl -I https://thehinterlandguide.app/privacy
curl -I https://thehinterlandguide.app/terms
curl -I https://thehinterlandguide.app/support
curl -I https://thehinterlandguide.app/contact
```

Check visible-page placeholders. Public pages should not expose `TODO`,
`FIXME`, or `PLACEHOLDER` unless the copy intentionally says a page is a pilot
draft:

```sh
for path in / /privacy /terms /support /contact; do
  curl -fsS "https://thehinterlandguide.app${path}" \
    | grep -Eio "TODO|FIXME|PLACEHOLDER" \
    && echo "placeholder found on ${path}"
done
```

Check the pilot CTA opens email:

1. Open `https://thehinterlandguide.app/`.
2. Activate `Request pilot access`.
3. Confirm the link opens an email to `support@thehinterlandguide.app`.
4. Confirm the subject is `Hinterland pilot access request`.
5. Confirm the body asks for adult contact/logistics only and includes the
   instruction not to include a child's full name.

## Automated Live Smoke

Run the no-dependency live smoke from `web/`:

```sh
cd web
npm run smoke:live
```

Optional custom domain:

```sh
LANDING_BASE_URL=https://www.thehinterlandguide.app npm run smoke:live
```

Local static preview:

```sh
python -m http.server 4175 --bind 127.0.0.1 --directory web/public
cd web
LANDING_BASE_URL=http://127.0.0.1:4175 npm run smoke:live
```

The local preview path falls back from `/privacy` to `/privacy.html` because
Python's static server does not emulate SWA clean URLs.

## Remaining Manual Gates

Before using the URLs for Closed/Open/Production store tracks:

- privacy and terms copy must be lawyer-reviewed
- `support@thehinterlandguide.app` must be monitored
- `privacy@thehinterlandguide.app` must be monitored
- Google Play data-safety and target-audience answers must be re-verified
- any future DNS/SWA domain binding change must be smoke-tested from this
  runbook
