"""Typed application settings."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal, cast
from urllib.parse import quote, quote_plus, urlparse

from fastapi import Request
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["local", "dev", "staging", "prod"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


def _default_cors_origins() -> list[str]:
    # Keep browser access explicit. The custom domain and the backing Static
    # Web Apps domain serve the same adult-only parent setup surface; neither
    # the kid app nor unrelated Hinterland sites need cross-origin API access.
    return [
        "http://localhost:19006",
        "https://parents.thehinterlandguide.app",
        "https://purple-coast-088e6b30f.7.azurestaticapps.net",
    ]


class Settings(BaseSettings):
    """Environment-driven Hinterland configuration."""

    model_config = SettingsConfigDict(
        env_prefix="HINTERLAND_",
        env_file=".env",
        extra="ignore",
        populate_by_name=True,
    )

    app_name: str = "Hinterland API"
    app_version: str = "0.1.0"
    env: Environment = "local"
    log_level: LogLevel = "INFO"
    cors_origins: list[str] = Field(default_factory=_default_cors_origins)

    photos_bucket: str = "photos"
    # W1 pilot environments require the same ULID on presign and finalization.
    observation_idempotency_required: bool = False

    # Expedition content root read by admin.sync_expeditions. The deployed
    # image ships the JSON at /app/content/expeditions (backend/Dockerfile
    # builds from the repo root and copies content/expeditions/ in). Local
    # runs override via HINTERLAND_CONTENT_ROOT -- the
    # scripts/sync_expeditions.py shim points it at the repo checkout.
    content_root: str = "/app/content/expeditions"

    # Object-storage backend. "noop" falls through to a fake impl in
    # tests (set explicitly via app.state.signed_url_generator).
    # "blob" uses Azure Blob Storage with user-delegation SAS URLs
    # minted via the Container Apps managed identity (Storage Blob
    # Data Contributor on the account).
    storage_provider: Literal["noop", "blob"] = "blob"
    blob_account_endpoint: str = ""

    # Microsoft Entra External ID -- adult auth. The mobile client requests
    # the api://hinterland-api/user.access scope, but Entra v2 access tokens
    # carry the API application's client ID (GUID) in ``aud``. The verifier is
    # JWKS-only via PyJWT; msal lives in the mobile client.
    entra_tenant_id: str = "18dbd7fa-c411-49bc-82fc-9ccaa26e3404"
    entra_api_audience: str = "7dd9da3c-b7d6-45d4-955b-d7561c43f209"
    entra_client_app_id: str = "60504e4c-6b5f-4031-a80a-3e4bdfae29b2"
    entra_required_scope: str = "user.access"
    entra_issuer: str = (
        "https://login.microsoftonline.com/18dbd7fa-c411-49bc-82fc-9ccaa26e3404/v2.0"
    )
    entra_jwks_url: str = (
        "https://login.microsoftonline.com/18dbd7fa-c411-49bc-82fc-9ccaa26e3404/discovery/v2.0/keys"
    )

    # Hinterland RS256 kid JWTs (handoff + session). Backend mints and
    # verifies these locally; the kid app stores the session JWT and sends
    # it as a Bearer token. JWKS published at /.well-known/...json.
    hinterland_jwt_issuer: str = Field(
        default="https://api.thehinterlandguide.app",
        validation_alias="HINTERLAND_KID_JWT_ISSUER",
    )
    hinterland_jwt_audience: str = Field(
        default="hinterland-api",
        validation_alias="HINTERLAND_KID_JWT_AUDIENCE",
    )
    hinterland_jwt_kid: str = Field(
        default="k1-2026-07",
        validation_alias="HINTERLAND_KID_JWT_KID",
    )
    hinterland_handoff_ttl_seconds: int = 900  # 15 minutes
    hinterland_session_ttl_seconds: int = 60 * 60 * 24 * 30  # 30 days

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
    # fail-closed: explicit override (`HINTERLAND_ALLOW_STUB_AUTH=true|false`)
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
    # (HINTERLAND_DEV_AUTH_ENABLED=true), a non-empty shared key must be
    # configured (HINTERLAND_DEV_AUTH_TOKEN), and `env == "prod"` 404s the
    # route regardless of both. Enabled-without-key is treated as a
    # misconfiguration: the route stays 404 and logs a warning.
    dev_login_enabled: bool = Field(
        default=False,
        validation_alias="HINTERLAND_DEV_AUTH_ENABLED",
    )
    dev_login_key: str | None = Field(
        default=None,
        validation_alias="HINTERLAND_DEV_AUTH_TOKEN",
    )

    # iNaturalist photo egress is independently default-deny. A token alone is
    # never permission to disclose a child's photo.
    inat_base_url: str = "https://api.inaturalist.org/v1"
    inat_oauth_token: str = ""
    inat_request_timeout_seconds: float = 8.0
    inat_cv_enabled: bool = False
    inat_cv_disclosure_approved: bool = False
    inat_cv_benchmark_approved: bool = False
    inat_cv_model_version: str = "inat-cv-v1"
    taxonomy_packs_bucket: str = "taxonomy-packs"

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
    # CV has the separate disclosure and benchmark gates above.
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
    # Dev / CI default to "noop", which records `pilot_private`; it is
    # explicitly not a safety clearance.
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

    # Azure Service Bus for the iNat-submit transactional outbox (Risk
    # 0002 closure). Namespace is the FQDN
    # (e.g. `hinterland-sb-dev.servicebus.windows.net`); empty namespace
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
    def inat_cv_egress_allowed(self) -> bool:
        """True only after product, disclosure, and benchmark approval."""
        return (
            self.inat_cv_enabled
            and self.inat_cv_disclosure_approved
            and self.inat_cv_benchmark_approved
        )

    @property
    def photo_helper_enabled(self) -> bool:
        """Kid-facing CV capability after every disclosure/config gate."""

        provider_url = urlparse(self.inat_base_url.strip())
        return bool(
            self.inat_cv_egress_allowed
            and provider_url.scheme == "https"
            and provider_url.netloc
            and self.inat_oauth_token.strip()
        )

    database_host: str = "localhost"
    database_port: int = 5432
    database_name: str = "hinterland"
    database_user: str = "hinterland"
    database_password: str = "hinterland"
    database_password_secret: str = ""
    database_pool_size: int = 5
    database_max_overflow: int = 2
    database_echo_sql: bool = False
    readiness_database_required: bool = False

    @property
    def database_configured(self) -> bool:
        return bool(self.database_host)

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
