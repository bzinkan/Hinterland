# Azure infrastructure

Sibling to `infra-gcp/`. Tracks Dragonfly's Azure-side resources during the GCP -> Azure migration (ADR 0010).

## What's here

This directory will hold the `az` CLI scripts and (eventually) Bicep / Terraform definitions for the Azure target architecture. During the migration we use `az` CLI imperatively against `--subscription 5a04114f-9102-4e0b-828b-b385096edfbc` and only codify the final shape once it's stable.

## Subscription + tenant

| | Value |
|---|---|
| Subscription ID | `5a04114f-9102-4e0b-828b-b385096edfbc` |
| Subscription name | Azure subscription 1 |
| Tenant ID | `3b7e8876-fd7e-4b71-b14f-f1bf9beb8e05` |
| Tenant default domain | `briandragonflyapp.onmicrosoft.com` |
| Resource group (dev) | `dragonfly-dev-rg` |
| Region | `eastus2` |

## CLI usage

Every command targets the Dragonfly subscription explicitly so it doesn't disturb other defaults:

```powershell
az group show --name dragonfly-dev-rg --subscription 5a04114f-9102-4e0b-828b-b385096edfbc
```

If you find yourself running many commands in a row, set a shell variable instead of changing the default:

```powershell
$SUB = "5a04114f-9102-4e0b-828b-b385096edfbc"
az group show --name dragonfly-dev-rg --subscription $SUB
```

## Phase tracking

See ADR 0010 for the phased plan. Each phase lands as its own PR.
