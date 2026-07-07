# Azure infrastructure

Tracks Hinterland's Azure-side resources. ADR 0010 selected Azure; ADR 0014
decommissions the old GCP/Firebase runtime and deploy paths.

## What's here

This directory holds `az` CLI scripts and notes for the Azure target
architecture. The active development environment is in the Gordi-backed Azure
subscription; the older Dragonfly migration subscription is historical.

## Subscription + tenant

| | Value |
|---|---|
| Subscription ID | `3ac5dfb0-91b7-47d3-8187-9dc8d6305e96` |
| Subscription name | Azure subscription 1 |
| Tenant ID | `18dbd7fa-c411-49bc-82fc-9ccaa26e3404` |
| Signed-in operator | `bzinkan@gordi.io` |
| Resource group (dev) | `hinterland-dev-rg` |
| Region | `eastus` for Container Apps; managed dependencies may vary by service |

## CLI usage

Every command targets the Hinterland subscription explicitly so it doesn't disturb other defaults:

```powershell
az group show --name hinterland-dev-rg --subscription 3ac5dfb0-91b7-47d3-8187-9dc8d6305e96
```

If you find yourself running many commands in a row, set a shell variable instead of changing the default:

```powershell
$SUB = "3ac5dfb0-91b7-47d3-8187-9dc8d6305e96"
az group show --name hinterland-dev-rg --subscription $SUB
```

## Phase tracking

See ADR 0010 for the original phased plan and ADR 0014 for the active
Firebase/GCP decommission decision.
