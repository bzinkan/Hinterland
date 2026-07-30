# Azure Environment Contracts

`hinterland-dev.env` is the non-secret source of truth for the active Azure
resource names. It is consumed by reviewed operational work only.

Every Azure operation must target `hinterland-dev-rg` in the configured Gordi
subscription. The environment contains no credentials; secrets belong in Key
Vault or GitHub Actions secrets.

The placement contract is intentionally split: the canonical API and
PostgreSQL are in Central US, while the rollback API, jobs, storage, Key Vault,
ACR, Service Bus, and Log Analytics remain in East US. Both API apps and every
job must use one immutable digest during the DNS rollback window.

`hinterland-qa.env` is a separate Adult QA contract, not a second dev/W1
configuration. Only the ACR, Log Analytics workspace, and PostgreSQL server may
be shared. QA uses its own database/role, resource group, identities, Key
Vault, storage, queues, Content Safety account, Entra registrations, kid keys,
API, and parent web app. Run the read-only cost/isolation preflight and retain
explicit operator cost approval before any billable provisioning.
