# Dragonfly Privacy Policy (DRAFT)

> **STATUS: DRAFT, NOT FOR PUBLICATION.** This captures the Azure-era product
> facts. Counsel must review the final text before closed/public store release.

Last updated: 2026-06-04 (draft)

## What Dragonfly is

Dragonfly is a citizen-science field app for curious explorers of all ages.
People photograph real outdoor organisms, build a personal Dex, complete expeditions, and may
eventually contribute approved observations to iNaturalist through a
Dragonfly-owned project account.

A parent or teacher creates the kid account. Kids do not enter email addresses,
do not see ads, and do not communicate through public chat, direct messages, or
kid-to-kid free text.

## What we collect from a kid account

- A photo of an organism, stored privately in Azure Blob Storage.
- Observation location. The Play Internal pilot uses coarse location.
- Species identification chosen by the kid, optionally suggested by iNaturalist.
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

Data is encrypted in transit and at rest. The landing site may still be served
from Firebase Hosting while ADR 0010 keeps apex/www hosting and DNS split during
the migration.

## What we do with the data

- **App functionality.** Show observations, Dex entries, expedition progress,
  rewards, review queue, and Sanctuary state.
- **Moderation.** Photos are reviewed asynchronously. The closed-beta target is
  Azure AI Content Safety; W1 Internal Testing may run with noop moderation and
  adult-supervised manual review only.
- **Scientific contribution.** iNaturalist public submission is off for W1
  Internal Testing. For closed beta and later, approved observations may be
  submitted through the Dragonfly project account, not under the kid's name.
- **Operations.** Structured logs help debug failures. Photo bytes are never
  logged.

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
data, photos, and future iNaturalist contributions remains an operator follow-up
until legal copy and retention policy are finalized.

Consent events are persisted in `parent_consent_records` with a receipt id,
parent email, policy version, and server timestamp. Raw IP and User-Agent are
not stored as consent fields.

## Contact

Privacy requests: **privacy@dragonfly-app.net**

Support: **support@dragonfly-app.net**
