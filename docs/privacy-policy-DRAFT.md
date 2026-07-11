# Hinterland Privacy Policy (DRAFT)

> **STATUS: DRAFT, NOT FOR PUBLICATION.** This captures the Azure-era product
> facts. Counsel must review the final text before closed/public store release.

Last updated: 2026-07-11 (draft)

## What Hinterland is

Hinterland is a citizen-science field app for curious explorers of all ages.
People photograph real outdoor organisms, build a personal Dex, and complete
Expeditions. Public iNaturalist contribution is not enabled for W1 or closed
beta and requires a separate consent/geoprivacy decision.

A parent or teacher creates the kid account. Kids do not enter email addresses,
do not see ads, and do not communicate through public chat, direct messages, or
kid-to-kid free text.

## What we collect from a kid account

- A photo of an organism, stored privately in Azure Blob Storage.
- An optional four-character coarse-area geohash for an observation. No
  location is valid; new clients discard raw coordinates before upload.
- A coarse location cell (a grid square roughly 20 by 40 kilometers, computed on the device) when the
  app suggests expeditions. Raw coordinates are not sent for this feature, and
  the app never asks for new location permission to do it.
- Species identification chosen from the project catalog, manual display text,
  or Unknown. Pre-save iNaturalist image suggestions are disabled.
- Timestamp, display name, age band, group membership, Dex/reward/Sanctuary
  progress.

We do not collect kid email, phone number, last name, exact birth date,
advertising IDs, contacts, microphone, calendar, SMS, or behavioral ad data.

## What we collect from a parent or teacher account

- Email address for sign-in through Microsoft Entra External Identities.
- Display name, owned groups, provisioned kid accounts, consent records.
- Standard service logs for debugging and security.

## Where data is stored

The active backend uses Azure:

- Azure Database for PostgreSQL Flexible Server for structured data.
- Azure Blob Storage for photos.
- Azure Container Apps for the API runtime.
- Azure Key Vault for secrets.
- Azure Monitor / Log Analytics for operational logs.

Data is encrypted in transit and at rest. The landing site and parents web app
are served from Azure Static Web Apps.

## What we do with the data

- **App functionality.** Show observations, Dex entries, expedition progress,
  rewards, review queue, and Sanctuary state. Rank suggested expeditions by
  the kid's general area using the coarse on-device location cell.
- **Moderation.** Photos are reviewed asynchronously. The closed-beta target is
  Azure AI Content Safety. W1 NoOp records `pilot_private`, is not a safety
  approval, grants no signed-photo access, and purges server-hosted photo bytes
  after seven days. Unsynced queue work stays on the original device until it
  syncs or an adult explicitly discards it.
- **Scientific contribution.** iNaturalist public submission is off for W1 and
  closed beta. Optional post-clean CV also stays off until reviewed disclosure,
  consent, and accuracy gates pass.
- **Operations.** Structured logs help debug failures. Photo bytes are never
  logged. Raw coordinates, signed photo URLs, and child-entered manual species
  text are not logged.

Photo containers are private. A child can receive a signed URL only for their
own clean photo; peer children cannot. Authorized managing adults may access a
clean photo and authorized reviewers may access a quarantined photo. Pending,
pilot-private, failed, rejected, and deleted photos receive no signed URL.

## What we do not do

- No selling, renting, advertising, ad targeting, or profiling.
- No third-party ad SDKs.
- No kid data used to train AI models.
- No kid-facing runtime LLM or multi-agent calls.
- No marketing emails or push notifications in Phase 1.

## COPPA / parent rights

Parents can request access, export, or deletion of their own account and linked
kid accounts. The app includes an account-deletion request button in Settings.
The immediate API effect is `users.disabled_at`; full deletion of linked child
data, photos, and any historical iNaturalist contributions remains an operator follow-up
until legal copy and retention policy are finalized.

Consent events are persisted in `parent_consent_records` with a receipt id,
parent email, policy version, and server timestamp. Raw IP and User-Agent are
not stored as consent fields. The consent browser generates a high-entropy
tab-scoped proof; only its SHA-256 digest is stored so an email address alone
cannot claim a receipt. The raw proof is never logged or retained by the API.

## Contact

Privacy requests: **privacy@thehinterlandguide.app**

Support: **support@thehinterlandguide.app**
