# Risk 0006: Web deploy DNS + hosting site (human action)

- **Status:** Open
- **Date filed:** 2026-05-10
- **Source:** Web adult dashboard follow-up 4 ("separate deploy at parents.dragonfly-app.net")
- **Owner:** Brian (Firebase Console + DNS provider clicks; no autonomous unblock path)

## What we have

- `mobile/firebase.json` + `.firebaserc` declare a hosting target `parents` -> site `dragonfly-parents-dev`.
- `.github/workflows/deploy-web-dev.yml` builds the Expo web bundle on push to `main` (when `mobile/**` changes) and deploys it via the Firebase CLI under the existing GitHub deploy SA.
- Terraform grants the deploy SA `roles/firebasehosting.admin` and enables `firebasehosting.googleapis.com`.

That's everything CI can do unattended. The site itself, the custom domain, and the DNS records require human clicks.

## Human action items

### 1. Initialise the Firebase project for Hosting

Once per project. From the Firebase Console for `dragonflyapp-495423`:

- [ ] Open https://console.firebase.google.com/project/dragonflyapp-495423/hosting
- [ ] If prompted, "Get started" (this just confirms Hosting is enabled; the API is already on via Terraform)

### 2. Create the Hosting site

The deploy workflow targets a site called `dragonfly-parents-dev`. The site has to exist before the first deploy or the CLI errors with "Site does not exist."

```bash
gcloud firebase hosting sites create dragonfly-parents-dev \
  --project dragonflyapp-495423
```

(Or use the Console: Hosting -> Add another site -> `dragonfly-parents-dev`.)

### 3. First deploy (smoke)

Trigger the workflow manually once to confirm the pipeline works before relying on push triggers:

- [ ] Actions -> "Deploy parent web (dev)" -> Run workflow on `main`
- [ ] Confirm the run lands a build at `https://dragonfly-parents-dev.web.app`
- [ ] Open that URL, sign in with the Firebase account from follow-up 3

### 4. Wire `parents.dragonfly-app.net`

In the Firebase Hosting Console for site `dragonfly-parents-dev`:

- [ ] Add custom domain -> `parents.dragonfly-app.net`
- [ ] Firebase shows two records to add at the DNS provider for `dragonfly-app.net` (typically a TXT for ownership + an A record pair for the apex)
- [ ] Add those records at the DNS provider
- [ ] Wait for Firebase to verify (usually <30 min, can be hours)
- [ ] Confirm `https://parents.dragonfly-app.net` serves the bundle and shows a Let's Encrypt cert

## Why this is human-only

Firebase Hosting site creation is an idempotent IaC-friendly operation in principle, but the Terraform provider's `google_firebase_hosting_site` resource still has rough edges around custom-domain resources, and the DNS records have to be added at whichever registrar/DNS provider holds `dragonfly-app.net` -- which is outside the GCP project. The 5 minutes of clicks beat building two more layers of automation for a one-time setup.

## Unblock condition

Risk closes when `https://parents.dragonfly-app.net` returns HTTP 200 with a valid TLS cert and the deployed bundle. After that, every push to `main` that touches `mobile/**` ships a new version automatically.
