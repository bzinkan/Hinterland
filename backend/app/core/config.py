"""Typed application settings."""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Any, Literal, cast
from urllib.parse import quote, quote_plus

from fastapi import Request
from pydantic import Field
from pydantic.fields import FieldInfo
from pydantic_settings import (
    BaseSettings,
    EnvSettingsSource,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

Environment = Literal["local", "dev", "staging", "prod"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


def _default_cors_origins() -> list[str]:
    return ["http://localhost:19006"]


_HINTERLAND_RENAMED_ENV_VARS = {
    "dev_login_enabled": "HINTERLAND_DEV_AUTH_ENABLED",
    "dev_login_key": "HINTERLAND_DEV_AUTH_TOKEN",
    "dragonfly_jwt_issuer": "HINTERLAND_KID_JWT_ISSUER",
    "dragonfly_jwt_audience": "HINTERLAND_KID_JWT_AUDIENCE",
    "dragonfly_jwt_kid": "HINTERLAND_KID_JWT_KID",
}


class HinterlandRenamedEnvSource(PydanticBaseSettingsSource):
    """Map renamed live Hinterland env vars to legacy Settings field names."""

    def get_field_value(
        self,
        field: FieldInfo,
        field_name: str,
    ) -> tuple[Any, str, bool]:
        env_name = _HINTERLAND_RENAMED_ENV_VARS.get(field_name)
        if env_name is None:
            return None, field_name, False
        value = os.environ.get(env_name)
        if value is None:
            return None, field_name, False
        return value, field_name, self.field_is_complex(field)

    def __call__(self) -> dict[str, Any]:
        data: dict[str, Any] = {}
        for field_name, field in self.settings_cls.model_fields.items():
            value, key, value_is_complex = self.get_field_value(field, field_name)
            if value is None:
                continue
            data[key] = self.prepare_field_value(
                field_name,
                field,
                value,
                value_is_complex,
            )
        return data


class Settings(BaseSettings):
    """Environment-driven configuration.

    `DRAGONFLY_` env vars remain supported during the rebrand overlap.
    The newer Hinterland Container Apps may use `HINTERLAND_` names.
    """

    model_config = SettingsConfigDict(
        env_prefix="DRAGONFLY_",
        env_file=".env",
        extra="ignore",
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (
            init_settings,
            env_settings,
            HinterlandRenamedEnvSource(settings_cls),
            EnvSettingsSource(settings_cls, env_prefix="HINTERLAND_"),
            dotenv_settings,
            file_secret_settings,
        )

    app_name: str = "Hinterland API"
    app_version: str = "0.1.0"
    env: Environment = "local"
    log_level: LogLevel = "INFO"
    cors_origins: list[str] = Field(default_factory=_default_cors_origins)

    photos_bucket: str = "photos"

    # Expedition content root read by admin.sync_expeditions. The deployed
    # image ships the JSON at /app/content/expeditions (backend/Dockerfile
    # builds from the repo root and copies content/expeditions/ in). Local
    # runs override via DRAGONFLY_CONTENT_ROOT -- the
    # scripts/sync_expeditions.py shim points it at the repo checkout.
    content_root: str = "/app/content/expeditions"

    # Object-storage backend. "noop" falls through to a fake impl in
    # tests (set explicitly via app.state.signed_url_generator).
    # "blob" uses Azure Blob Storage with user-delegation SAS URLs
    # minted via the Container Apps managed identity (Storage Blob
    # Data Contributor on the account).
    storage_provider: Literal["noop", "blob"] = "blob"
    blob_account_endpoint: str = ""

    # Microsoft Entra External ID -- adult auth. The active Hinterland tenant
    # lives at 18dbd7fa-c411-49bc-82fc-9ccaa26e3404 and the API audience is
    # registered as "api://hinterland-api". The verifier is
    # JWKS-only via PyJWT; msal lives in the mobile client.
    entra_tenant_id: str = "18dbd7fa-c411-49bc-82fc-9ccaa26e3404"
    entra_api_audience: str = "api://hinterland-api"
    entra_issuer: str = (
        "https://login.microsoftonline.com/18dbd7fa-c411-49bc-82fc-9ccaa26e3404/v2.0"
    )
    entra_jwks_url: str = (
        "https://login.microsoftonline.com/18dbd7fa-c411-49bc-82fc-9ccaa26e3404/discovery/v2.0/keys"
    )

    # Hinterland RS256 kid JWTs (handoff + session). Backend mints and
    # verifies these locally; the kid app stores the session JWT and sends
    # it as a Bearer token. JWKS published at /.well-known/...json.
    dragonfly_jwt_issuer: str = "https://api.thehinterlandguide.app"
    dragonfly_jwt_audience: str = "hinterland-api"
    dragonfly_jwt_kid: str = "k1-2026-07"
    dragonfly_handoff_ttl_seconds: int = 900  # 15 minutes
    dragonfly_session_ttl_seconds: int = 60 * 60 * 24 * 30  # 30 days

    # Azure Key Vault holding the kid-JWT signing PEM (RS256). Read once
    # per process via DefaultAzureCredential (UAMI in Container Apps).
    key_vault_url: str = "https://hinterland-kv-dev.vault.azure.net/"
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

    # Test-compat stub auth. When allowed, bearer claims lacking both the
    # ``oid`` (Entra) and ``token_type`` (Hinterland) markers short-circuit
    # to a claims-only CurrentUser with no DB lookup -- a shape only the
    # test suite's stubbed verifiers produce. Production-safe default is
    # fail-closed: explicit override (`DRAGONFLY_ALLOW_STUB_AUTH=true|false`)
    # wins; otherwise the shortcut is permitted on `local` only.
    allow_stub_auth: bool | None = None

    @property
    def stub_auth_allowed(self) -> bool:
        """True when the test-compat stub-claims shortcut may be taken."""
        if self.allow_stub_auth is not None:
            return self.allow_stub_auth
        return self.env == "local"

    # Dev-only auto-login for pre-production mobile builds
    # (`POST /v1/auth/dev-login`, hidden from the OpenAPI schema). The
    # dev API and the W1 pilot share one deployment, so this is
    # FAIL-CLOSED three ways: the flag must be explicitly enabled
    # (DRAGONFLY_DEV_LOGIN_ENABLED=true), a non-empty shared key must be
    # configured (DRAGONFLY_DEV_LOGIN_KEY), and `env == "prod"` 404s the
    # route regardless of both. Enabled-without-key is treated as a
    # misconfiguration: the route stays 404 and logs a warning.
    dev_login_enabled: bool = False
    dev_login_key: str | None = None

    # iNaturalist API integration. Token is empty in dev / CI; the iNat client
    # treats absence of a token as "no third-party calls allowed" and CV
    # endpoints return a `cv_unavailable` flag instead of raising.
    inat_base_url: str = "https://api.inaturalist.org/v1"
    inat_oauth_token: str = ""
    inat_request_timeout_seconds: float = 8.0

    # Optional non-LLM fallback when iNaturalist returns no usable species
    # suggestions. This deliberately returns display-only organism labels
    # (for example "Dog") without taxon IDs, so Dex/rarity rewards remain
    # grounded in iNat taxa.
    organism_fallback_provider: Literal["noop", "azure_vision"] = "noop"
    organism_fallback_min_confidence: float = 0.55
    azure_vision_endpoint: str = ""
    azure_vision_key: str = ""
    azure_vision_request_timeout_seconds: float = 8.0

    # Outbound iNat submission posture. Defaults to False per the
    # Option B decision (2026-06-04): Hinterland does NOT post kid
    # observations to iNaturalist while the kid is under 13 because
    # iNat's standard ToS requires users to be 13+. Observations stay
    # in Hinterland until the kid claims them via the Phase 3 age-13
    # iNat-claim flow.
    #
    # When False the moderation worker / review-queue approve handler
    # skip writing the `inat_submit_outbox` row entirely; the Service
    # Bus consumer is dormant and the queue stays empty. The
    # infrastructure (queues, jobs, alerts) stays provisioned so
    # flipping this flag back on is a zero-deploy operator action.
    #
    # iNat CV (the read-only species identify endpoint) is unaffected
    # -- it never posts; the `inat_oauth_token` setting still wires
    # rate limits for it.
    inat_submit_enabled: bool = False

    # Reverse-geocoding provider. "noop" returns None for every lookup --
    # the kid sees no place_name; the observation itself still saves.
    # "nominatim" hits the public Nominatim instance (1 req/sec, no
    # commercial use); fine for dev. Production needs a contracted
    # provider (Google Maps, self-hosted Nominatim) per `docs/runbook.md`.
    geocoding_provider: Literal["noop", "nominatim"] = "noop"
    geocoding_nominatim_base_url: str = "https://nominatim.openstreetmap.org"
    geocoding_user_agent: str = "Hinterland/0.1 (+https://thehinterlandguide.app)"
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

    # Internal-route OIDC auth. The `/internal/*` routes are called by
    # platform infrastructure. Production-safe default is
    # fail-closed: if `internal_oidc_required` is left None, the
    # `require_internal_oidc` property requires OIDC on any env that
    # isn't `local`. Local dev opts out so smoke scripts + the moderation
    # processor unit tests don't need a Google identity.
    internal_oidc_required: bool | None = None
    internal_oidc_audience: str = ""
    internal_oidc_allowed_service_accounts: list[str] = Field(default_factory=list)

    # Azure Service Bus for the iNat-submit transactional outbox (Risk
    # 0002 closure). Namespace is the FQDN
    # (e.g. `dragonfly-sb-dev.servicebus.windows.net`); empty namespace
    # means "Service Bus not provisioned yet" -- the enqueue helper
    # returns success=False with `not_configured`, the outbox row stays
    # `pending`, and the 15-min replay job picks it up once provisioning
    # lands. Production auth is the Container App managed identity via
    # DefaultAzureCredential (no connection string).
    service_bus_namespace: str = ""
    service_bus_inat_queue: str = "inat-submit"
    service_bus_moderation_queue: str = "moderation-pending"
    service_bus_request_timeout_seconds: float = 8.0
    # Max messages a Service Bus consumer pulls per receive call. Keep
    # small so a stuck handler doesn't lock too many messages at once.
    service_bus_receive_batch_size: int = 8
    # Per-message lock duration grant; renewed on each receive cycle.
    # Service Bus default is 60s, but the moderation worker can take a
    # full Azure Content Safety roundtrip + Blob copy on slow images.
    service_bus_receive_max_wait_seconds: float = 30.0

    @property
    def service_bus_enabled(self) -> bool:
        """True when the producer can attempt to enqueue Service Bus messages.

        Empty namespace is the explicit "not provisioned yet" signal --
        producers gracefully no-op and leave outbox rows in `pending`
        for the replay job to retry once infra catches up.
        """
        return bool(self.service_bus_namespace)

    @property
    def require_internal_oidc(self) -> bool:
        """True when internal routes must enforce platform OIDC.

        Explicit override (`DRAGONFLY_INTERNAL_OIDC_REQUIRED=true|false`)
        wins. Otherwise, anything past `local` fails closed.
        """
        if self.internal_oidc_required is not None:
            return self.internal_oidc_required
        return self.env != "local"

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
        """Build an async SQLAlchemy URL for local or managed Postgres."""
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
