# Phase 9 — Async pipeline + monitoring

Closes the **infra-azure** half of Risk 0002 (async safety/science pipeline) and the alarms half of Risk 0003 (dispatcher SLO). All Hinterland app code that consumes this infra already landed in PRs #107–#112 on `main`; phase 9 wires the Azure-side resources those app changes expect.

## What ships

| Script | Provisions |
|---|---|
| [`phase-9-async-pipeline.sh`](phase-9-async-pipeline.sh) | 3 empty KV secrets; Service Bus namespace + 2 queues with DLQ; Event Grid system topic + subscription → `moderation-pending`; UAMI role assignments for SB Data Sender/Receiver + the Event Grid topic's identity SB Data Sender; 6 Container Apps Jobs (2 event-driven workers + 4 cron jobs); Container App env-var update with async, DB, and Blob vars |
| [`phase-9-monitoring.sh`](phase-9-monitoring.sh) | 1 action group (email receiver); 4 alerts: dispatcher p95 > 300 ms, DLQ depth on both queues > 0, scheduled-job failure |

## Run order

```bash
# Prerequisite: phase-5 has already run (UAMI + Container App + Key Vault + Log Analytics in place)

MSYS_NO_PATHCONV=1 bash infra-azure/phase-9-async-pipeline.sh

# Populate the 3 KV placeholder secrets out of band:
az keyvault secret set --vault-name dragonfly-kv-dev --name inat-oauth-token        --value "<from iNat>"
az keyvault secret set --vault-name dragonfly-kv-dev --name content-safety-endpoint --value "https://<your-cs>.cognitiveservices.azure.com/"
az keyvault secret set --vault-name dragonfly-kv-dev --name content-safety-key      --value "<from Azure portal>"

# Restart the Container App so the new env-vars + secret refs pick up
az containerapp revision restart \
  --name dragonfly-api \
  --resource-group dragonfly-dev-rg

# Wire the alerts (set DRAGONFLY_ALERT_EMAIL or accept the default)
MSYS_NO_PATHCONV=1 DRAGONFLY_ALERT_EMAIL=you@example.com bash infra-azure/phase-9-monitoring.sh
```

## Verifications (Stream D)

| What | How | Pass criterion |
|---|---|---|
| Event Grid → SB → moderation worker | Submit/upload one observation through the normal presign path so a DB photo row exists and Blob lands under `photos/pending/` | Within ~30 s: `az containerapp job execution list --name dragonfly-moderation-worker --resource-group dragonfly-dev-rg` shows a successful execution; `observations.moderation_status` flips to `clean` or `quarantine` |
| Service Bus → iNat submit | Submit one observation via the mobile app (after iNat OAuth token is live) | iNat dashboard shows the observation within ~5 min; `inat_submit_outbox.status='submitted'` |
| Each cron job | `az containerapp job start --name dragonfly-rarity-refresh --resource-group dragonfly-dev-rg` (repeat for sweep, replay, dispatcher_replay) | Each exits 0 |
| Cron schedules fire | Wait for next `*/15` or `0 3 * * *` tick | Execution visible in `az containerapp job execution list` |
| Dispatcher p95 alert | Temporarily lower the threshold in [`phase-9-monitoring.sh`](phase-9-monitoring.sh), then replay/submit observations until `dispatcher.complete` logs are emitted | Alert email arrives within 10 min |
| DLQ depth alert | Use Azure Portal Service Bus Explorer, or a one-off SDK sender, to send `<malformed>` to `inat-submit`, then start `dragonfly-inat-submit-worker` | Worker dead-letters the parse failure immediately; alert email arrives within 10 min after DLQ count goes positive |

## What still needs human action (outside this script)

| Risk | Item |
|---|---|
| 0001 | Register iNat account + project; obtain OAuth token; populate `inat-oauth-token` KV secret; capture the 50-photo benchmark CSV with ≥ 70 % top-3 hit rate |
| 0002 | Provision Azure AI Content Safety resource; populate `content-safety-endpoint` + `content-safety-key` KV secrets |
| 0003 | Real-traffic p95 measurement over ≥ 50 real observations during dogfood week — irreducible, can only close after beta traffic exists |
| 0005 | Generated privacy policy + ToS published; `support@` and `privacy@` aliases live; first beta family identified |

## Notes / quirks

- **`MSYS_NO_PATHCONV=1`** is required on Git Bash on Windows so resource IDs starting with `/subscriptions/...` aren't path-converted.
- **Role assignment propagation** is eventually-consistent; if the moderation worker's first run hits a SB 403 right after the script finishes, give it 30–60 s and retry.
- **Event Grid system topic identity** auto-provisions when `--identity systemassigned` is set. The script handles the case where the identity is `null` on a brand-new topic.
- **`az monitor scheduled-query create` syntax** has had churn across `az` CLI versions; if it errors on your build, the alert can be created via the portal or `az rest` against the `microsoft.insights/scheduledqueryrules` API.
- **No real cost surprises**: Service Bus Standard ~$10/mo; Event Grid system topic free (no event volume yet); Container Apps Jobs charged only when running — cron jobs at 15-min cadence are well under $5/mo.
