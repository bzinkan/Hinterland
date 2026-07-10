# iNaturalist token refresh (dormant post-clean tooling)

W1 and closed beta keep iNaturalist CV and public submission disabled. A token
in Key Vault is not permission for child-photo egress. This utility remains for
an eventual legally approved, benchmarked, post-clean CV staging test only.

Do not run it for W1. Before any future use, Risk 0001 must close and
`INAT_CV_ENABLED`, `INAT_CV_DISCLOSURE_APPROVED`, and
`INAT_CV_BENCHMARK_APPROVED` must be reviewed independently. Public submit
remains outside this workflow.

## The daily ritual

1. **Open** [`https://www.inaturalist.org/users/api_token`](https://www.inaturalist.org/users/api_token) in a browser where you're signed in to iNat
2. **Select all** (Ctrl+A) → **copy** (Ctrl+C). You can copy the whole JSON `{"api_token":"..."}` — the script strips the envelope
3. **Run the script** from the repo root:
   ```bash
   bash scripts/refresh-inat-token.sh --clipboard
   ```
4. **Keep runtime gates false** until the approved post-clean staging probe.

That's it. The script writes the JWT to Key Vault, rolls the Container App revision, and prints when the new token expires.

## Current targets

The script defaults to the current Hinterland dev API target:

```bash
INAT_REFRESH_VAULT=hinterland-kv-dev
INAT_REFRESH_APP=hinterland-api
INAT_REFRESH_RG=hinterland-dev-rg
INAT_REFRESH_SECRET=inat-oauth-token
```

The Container App receives the secret as `HINTERLAND_INAT_OAUTH_TOKEN`.

```bash
INAT_REFRESH_VAULT=hinterland-kv-dev \
INAT_REFRESH_APP=hinterland-api \
INAT_REFRESH_RG=hinterland-dev-rg \
  bash scripts/refresh-inat-token.sh --clipboard
```

Approved post-clean staging smoke only:

1. Use only consented benchmark bytes already marked clean/adult-approved.
2. Call the post-save identify endpoint and confirm cache/model-version state.
3. Confirm every non-clean lifecycle state fails before provider egress.
4. Restore all three CV gates to false after the bounded staging probe.

## Making the rotation a habit

Pick whichever fits your day:

- **Google Calendar daily reminder** at the time you usually open your laptop ("Refresh Hinterland iNat token — 10s")
- **Windows Task Scheduler / cron** event that just pops a notification, since the actual rotation needs the browser session
- **Add the command to your shell's startup script** so a banner reminds you (`echo "TODO: bash scripts/refresh-inat-token.sh --clipboard"` in `.bashrc`)

The kid app does not depend on this token. Catalog/manual/Unknown remains the
W1 path.

## When this script gets retired

Once iNat approves the Hinterland OAuth app (account-age + improving-ID gates per https://www.inaturalist.org/oauth/applications/new):

1. Register the OAuth app, capture `client_id` + `client_secret`
2. Store both in Key Vault as `inat-oauth-client-id` + `inat-oauth-client-secret`
3. Replace this script with a Container Apps Job using the password grant (long-lived bearer tokens, with no daily rotation)
4. Delete this README

Path to OAuth-app eligibility:
- ✅ Confirm email — done
- ⏱️ Wait 2 months for account age
- ⏱️ Make 10 "improving identifications" in the last month (this is the citizen-science contribution side of iNat — adding more-specific IDs to other people's unidentified observations; recommended pace: a few a week)
- ⏱️ Submit the OAuth app application; iNat staff reviews; typically a few days

Realistic timeline: **early August** for OAuth-app + long-lived tokens.

## Input modes the script accepts

| Mode | Use when | Command |
|---|---|---|
| `stdin` (default) | Pasting into a fresh terminal | `bash scripts/refresh-inat-token.sh` then paste + Ctrl+D (Ctrl+Z + Enter on Git Bash) |
| `--clipboard` | Token's on your clipboard from the browser copy | `bash scripts/refresh-inat-token.sh --clipboard` |
| `--file /path` | Token's already in a temp file | `bash scripts/refresh-inat-token.sh --file /tmp/token.txt` |

## Environment overrides

For a different Key Vault / Container App / resource group (e.g. promoting to a staging environment):

```bash
INAT_REFRESH_VAULT=hinterland-kv-staging \
INAT_REFRESH_APP=hinterland-api-staging \
INAT_REFRESH_RG=hinterland-staging-rg \
  bash scripts/refresh-inat-token.sh --clipboard
```

## Why this isn't a Container Apps Job

I'd love for this to be fully automatic — `*/0 6 * * *` cron, no human input. But iNat's `/users/api_token` endpoint only accepts browser cookies, not service-account credentials. The two alternatives both have problems:

1. **Session-cookie scraping** — extract your `_inaturalist_session` cookie, store it in Key Vault, hit `/users/api_token` from a cron job. Technically works, but: cookies expire unpredictably (1 day to several months), iNat's anti-bot stack (Cloudflare) may flag a service-IP hitting authenticated endpoints, and arguably skirts iNat's "no automated access to authenticated pages" stance.

2. **OAuth password grant** — needs the registered OAuth app, which is the same 2-month gate above.

So the honest answer is: **the script is the closest thing to "automatic" you can ship today without OAuth-app eligibility**, and once that gate is past, the script becomes a Container Apps cron job in about half a day of work.
