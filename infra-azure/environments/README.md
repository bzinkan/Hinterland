# Azure environment contracts

These non-secret files centralize resource names used by safety-sensitive
operator scripts. They do not contain credentials. Current phase scripts and
the active deployment target Hinterland resources; compatibility names remain
only where ADR 0013 requires them.

Every script must pass an explicit subscription/resource group and refuse
`gordi-pilot-rg`. Sharing the Gordi subscription means billing only, never
runtime resources.
