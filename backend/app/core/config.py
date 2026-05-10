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

    gcp_project_id: str = "dragonflyapp-495423"
    photos_bucket: str = "dragonfly-photos-local"
    storage_emulator_host: str = ""

    firebase_project_id: str = ""
    firebase_check_revoked: bool = True

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
