# Azure Environment Contracts

`hinterland-dev.env` is the non-secret source of truth for the active Azure
resource names. It is consumed by reviewed operational work only.

Every Azure operation must target `hinterland-dev-rg` in the configured Gordi
subscription. The environment contains no credentials; secrets belong in Key
Vault or GitHub Actions secrets.
