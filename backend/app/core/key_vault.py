"""Thin wrapper over Azure Key Vault for Phase 6 boot-time secret loading.

The backend reads four named secrets from the Hinterland Key Vault:

* ``kid-jwt-signing-key``  -- RSA private PEM used to mint Hinterland RS256
  handoff + session JWTs for kids.
* ``kid-jwt-public-key``   -- RSA public PEM used to verify the same and to
  build the public JWKS at ``/.well-known/dragonfly-kid-jwks.json``.
* ``entra-tenant-id``      -- Microsoft Entra External ID tenant GUID. Falls
  back to ``Settings.entra_tenant_id`` when absent.
* ``entra-api-app-id``     -- The API app registration's audience claim. Falls
  back to ``Settings.entra_api_audience``.

Authentication uses ``DefaultAzureCredential``, which transparently picks up
the User-Assigned Managed Identity (UAMI) ``hinterland-api-mi`` inside
Container Apps and falls back to the local Azure CLI for dev runs.

For tests (and any environment without AKV access) the loader checks the
``Settings.kid_jwt_signing_pem`` / ``Settings.kid_jwt_public_pem`` env-var
shadows first; those take precedence so unit tests never reach the network.

Module-level ``lru_cache`` ensures each secret is fetched at most once per
process, so the first authenticated request after deploy pays the ~200ms
AKV round-trip and every subsequent request is in-memory.
"""

from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:  # pragma: no cover -- pulled in only for type checking.
    from app.core.config import Settings

log = structlog.get_logger()


class KidJwtSecretsUnavailable(RuntimeError):
    """Raised when neither AKV nor local env vars yield a kid-JWT PEM."""


def _build_secret_client(vault_url: str):  # type: ignore[no-untyped-def]
    """Construct a ``SecretClient`` bound to ``DefaultAzureCredential``.

    Imports are deferred so the module is importable without the Azure SDK
    wheels installed (tests typically use the env-var fallback path and
    never hit this code).
    """
    from azure.identity import DefaultAzureCredential
    from azure.keyvault.secrets import SecretClient

    credential = DefaultAzureCredential()
    return SecretClient(vault_url=vault_url, credential=credential)


@lru_cache(maxsize=8)
def _get_secret_value(vault_url: str, secret_name: str) -> str:
    """Fetch a single AKV secret value with per-(vault, name) memoization."""
    client = _build_secret_client(vault_url)
    secret = client.get_secret(secret_name)
    value = secret.value
    if value is None:
        raise KidJwtSecretsUnavailable(f"Key Vault secret {secret_name!r} has no value")
    # `azure-keyvault-secrets` types `value` as `str | None`; we've eliminated
    # the None branch above, so the return is unambiguously str -- but mypy
    # still infers Any when the SDK package lacks a `py.typed` marker.
    return str(value)


def get_kid_signing_pem(settings: Settings) -> bytes:
    """Return the PEM-encoded RSA private key used to mint Hinterland JWTs.

    Local override (``DRAGONFLY_KID_JWT_SIGNING_PEM``) wins so tests can
    supply a freshly generated key without reaching AKV.
    """
    if settings.kid_jwt_signing_pem:
        return settings.kid_jwt_signing_pem.encode("utf-8")
    try:
        value = _get_secret_value(
            settings.key_vault_url,
            settings.key_vault_kid_signing_secret,
        )
    except Exception as exc:  # bubble through with context.
        raise KidJwtSecretsUnavailable(
            "kid-jwt signing PEM unavailable (no env override and AKV fetch failed)"
        ) from exc
    return value.encode("utf-8")


def get_kid_public_pem(settings: Settings) -> bytes:
    """Return the PEM-encoded RSA public key for Hinterland-JWT verification."""
    if settings.kid_jwt_public_pem:
        return settings.kid_jwt_public_pem.encode("utf-8")
    try:
        value = _get_secret_value(
            settings.key_vault_url,
            settings.key_vault_kid_public_secret,
        )
    except Exception as exc:
        raise KidJwtSecretsUnavailable(
            "kid-jwt public PEM unavailable (no env override and AKV fetch failed)"
        ) from exc
    return value.encode("utf-8")


def get_entra_tenant_id(settings: Settings) -> str:
    """Return the Entra tenant ID, preferring AKV when available.

    AKV is treated as authoritative; the settings default is the bootstrap
    value used during initial onboarding before the secret is provisioned.
    """
    try:
        return _get_secret_value(settings.key_vault_url, "entra-tenant-id")
    except Exception:  # fall through to settings default.
        return settings.entra_tenant_id


def get_entra_api_app_id(settings: Settings) -> str:
    """Return the Entra API app registration's audience claim."""
    try:
        return _get_secret_value(settings.key_vault_url, "entra-api-app-id")
    except Exception:
        return settings.entra_api_audience


def clear_cache() -> None:
    """Drop the per-process secret cache. Intended for tests only."""
    _get_secret_value.cache_clear()
