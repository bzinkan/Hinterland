"""Typed application settings."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal, cast
from urllib.parse import quote, quote_plus

from fastapi import Request
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["local", "dev", "staging", "prod"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


def _default_cors_origins() -> list[str]:
    return ["http://localhost:19006"]


class Settings(BaseSettings):
    """Environment-driven configuration.

    Cloud Run env vars should keep the `DRAGONFLY_` prefix. Secret env vars
    should hold Secret Manager resource names, not secret values.
    """

    model_config = SettingsConfigDict(
        env_prefix="DRAGONFLY_",
        env_file=".env",
        extra="ignore",
    )

    app_name: str = "Dragonfly API"
    app_version: str = "0.1.0"
    env: Environment = "local"
    log_level: LogLevel = "INFO"
    cors_origins: list[str] = Field(default_factory=_default_cors_origins)

    photos_bucket: str = "photos"

    # Object-storage backend. "noop" falls through to a fake impl in
    # tests (set explicitly via app.state.signed_url_generator).
    # "blob" uses Azure Blob Storage with user-delegation SAS URLs
    # minted via the Container Apps managed identity (Storage Blob
    # Data Contributor on the account).
    storage_provider: Literal["noop", "blob"] = "blob"
    blob_account_endpoint: str = ""

    # Microsoft Entra External ID (formerly Azure AD B2C) -- Phase 6 adult
    # auth. The tenant lives at dfd7ebb4-0b29-42cb-aa05-e5e0124bab8f and the
    # API audience is registered as "api://dragonfly-api". The verifier is
    # JWKS-only via PyJWT; msal lives in the mobile client.
    entra_tenant_id: str = "dfd7ebb4-0b29-42cb-aa05-e5e0124bab8f"
    entra_api_audience: str = "api://dragonfly-api"
    entra_issuer: str = (
        "https://login.microsoftonline.com/dfd7ebb4-0b29-42cb-aa05-e5e0124bab8f/v2.0"
    )
    entra_jwks_url: str = (
        "https://login.microsoftonline.com/dfd7ebb4-0b29-42cb-aa05-e5e0124bab8f/discovery/v2.0/keys"
    )

    # Dragonfly RS256 kid JWTs (handoff + session). Backend mints and
    # verifies these locally; the kid app stores the session JWT and sends
    # it as a Bearer token. JWKS published at /.well-known/...json.
    dragonfly_jwt_issuer: str = "https://api.dragonfly-app.net"
    dragonfly_jwt_audience: str = "dragonfly-api"
    dragonfly_jwt_kid: str = "k1-2026-06"
    dragonfly_handoff_ttl_seconds: int = 900  # 15 minutes
    dragonfly_session_ttl_seconds: int = 60 * 60 * 24 * 30  # 30 days

    # Azure Key Vault holding the kid-JWT signing PEM (RS256). Read once
    # per process via DefaultAzureCredential (UAMI in Container Apps).
    key_vault_url: str = "https://dragonfly-kv-dev.vault.azure.net/"
    key_vault_kid_signing_secret: str = "kid-jwt-signing-key"
    key_vault_kid_public_secret: str = "kid-jwt-public-key"

    # Local fallbacks for tests / dev runs without Key Vault access. When
    # set, key_vault reads from these env vars instead of hitting AKV.
    kid_jwt_signing_pem: str = ""
    kid_jwt_public_pem: str = ""

    # Backend-augmented claim cache (Option C). Lookups for role +
    # group_id are short-circuited from a per-process TTLCache to keep
    # the per-request DB hit bounded.
    user_claims_cache_ttl_seconds: float = 30.0
    user_claims_cache_max_size: int = 1024

    # iNaturalist API integration. Token is empty in dev / CI; the iNat client
    # treats absence of a token as "no third-party calls allowed" and CV
    # endpoints return a `cv_unavailable` flag instead of raising.
    inat_base_url: str = "https://api.inaturalist.org/v1"
    inat_oauth_token: str = ""
    inat_request_timeout_seconds: float = 8.0

    # Reverse-geocoding provider. "noop" returns None for every lookup --
    # the kid sees no place_name; the observation itself still saves.
    # "nominatim" hits the public Nominatim instance (1 req/sec, no
    # commercial use); fine for dev. Production needs a contracted
    # provider (Google Maps, self-hosted Nominatim) per `docs/runbook.md`.
    geocoding_provider: Literal["noop", "nominatim"] = "noop"
    geocoding_nominatim_base_url: str = "https://nominatim.openstreetmap.org"
    geocoding_user_agent: str = "Dragonfly/0.1 (+https://dragonfly-app.net)"
    geocoding_request_timeout_seconds: float = 5.0

    # Photo moderation. Production gate is Azure AI Content Safety.
    # Dev / CI default to "noop" -- every photo is treated as clean.
    moderation_provider: Literal["noop", "azure_content_safety"] = "noop"

    # Azure AI Content Safety wiring (Phase 6c). Severity 0-6: 4 / Medium
    # is the quarantine threshold per ADR 0010. Endpoint + key are pulled
    # from Key Vault secrets `content-safety-endpoint` /
    # `content-safety-key` and surfaced as env vars at Container App
    # boot time.
    content_safety_endpoint: str = ""
    content_safety_key: str = ""
    content_safety_severity_threshold: int = 4
    content_safety_request_timeout_seconds: float = 8.0

    cloud_sql_instance: str = ""
    database_host: str = "localhost"
    database_port: int = 5432
    database_name: str = "dragonfly"
    database_user: str = "dragonfly"
    database_password: str = "dragonfly"
    database_password_secret: str = ""
    database_pool_size: int = 5
    database_max_overflow: int = 2
    database_echo_sql: bool = False
    readiness_database_required: bool = False

    @property
    def database_configured(self) -> bool:
        return bool(self.cloud_sql_instance or self.database_host)

    @property
    def sqlalchemy_database_url(self) -> str:
        """Build an async SQLAlchemy URL for local Postgres or Cloud SQL sockets."""
        user = quote_plus(self.database_user)
        password = quote_plus(self.database_password)
        database = quote_plus(self.database_name)

        if self.database_host.startswith("/"):
            socket_host = quote(self.database_host, safe="")
            return f"postgresql+asyncpg://{user}:{password}@/{database}?host={socket_host}"

        host = quote_plus(self.database_host)
        return f"postgresql+asyncpg://{user}:{password}@{host}:{self.database_port}/{database}"


@lru_cache
def get_settings() -> Settings:
    return Settings()


def get_request_settings(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)
