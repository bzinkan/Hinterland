import pytest

from app.core.config import Settings


def test_settings_accept_typed_environment_overrides() -> None:
    settings = Settings(
        env="dev",
        log_level="DEBUG",
        cors_origins=["http://localhost:8081"],
        database_port=6543,
        readiness_database_required=True,
    )

    assert settings.env == "dev"
    assert settings.log_level == "DEBUG"
    assert settings.cors_origins == ["http://localhost:8081"]
    assert settings.database_port == 6543
    assert settings.database_configured is True
    assert settings.readiness_database_required is True


def test_default_cors_origins_are_exact_and_parent_only() -> None:
    assert Settings().cors_origins == [
        "http://localhost:19006",
        "https://parents.thehinterlandguide.app",
        "https://purple-coast-088e6b30f.7.azurestaticapps.net",
    ]
    assert "*" not in Settings().cors_origins


def test_hinterland_environment_remains_supported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HINTERLAND_INAT_CV_ENABLED", raising=False)
    monkeypatch.setenv("HINTERLAND_INAT_CV_ENABLED", "true")

    assert Settings().inat_cv_enabled is True
    assert Settings().inat_cv_egress_allowed is False


def test_inat_photo_egress_defaults_disabled() -> None:
    settings = Settings()

    assert settings.inat_cv_enabled is False
    assert settings.inat_submit_enabled is False
    assert settings.observation_idempotency_required is False


def test_entra_v2_audience_is_the_api_client_id() -> None:
    settings = Settings()

    assert settings.entra_api_audience == "7dd9da3c-b7d6-45d4-955b-d7561c43f209"
    assert settings.entra_api_audience != "api://hinterland-api"
    assert settings.entra_client_app_id == "60504e4c-6b5f-4031-a80a-3e4bdfae29b2"
    assert settings.entra_required_scope == "user.access"


def test_cv_requires_enable_disclosure_and_benchmark_gates() -> None:
    settings = Settings(
        inat_cv_enabled=True,
        inat_cv_disclosure_approved=True,
        inat_cv_benchmark_approved=True,
    )
    assert settings.inat_cv_egress_allowed is True


def test_hinterland_can_require_observation_idempotency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HINTERLAND_OBSERVATION_IDEMPOTENCY_REQUIRED", "true")

    assert Settings().observation_idempotency_required is True


def test_content_root_defaults_to_image_path() -> None:
    # backend/Dockerfile bakes content/expeditions/ into the image here;
    # local runs override via HINTERLAND_CONTENT_ROOT (see the
    # scripts/sync_expeditions.py shim).
    assert Settings().content_root == "/app/content/expeditions"
    assert Settings(content_root="/tmp/expeditions").content_root == "/tmp/expeditions"


def test_dev_login_settings_default_off() -> None:
    settings = Settings()
    assert settings.dev_login_enabled is False
    assert settings.dev_login_key is None


def test_dev_login_settings_read_hinterland_env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HINTERLAND_DEV_AUTH_ENABLED", "true")
    monkeypatch.setenv("HINTERLAND_DEV_AUTH_TOKEN", "shared-key")
    settings = Settings()
    assert settings.dev_login_enabled is True
    assert settings.dev_login_key == "shared-key"


def test_settings_read_hinterland_env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HINTERLAND_ENV", raising=False)
    monkeypatch.delenv("HINTERLAND_DEV_AUTH_ENABLED", raising=False)
    monkeypatch.delenv("HINTERLAND_DEV_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("HINTERLAND_KID_JWT_ISSUER", raising=False)
    monkeypatch.setenv("HINTERLAND_ENV", "dev")
    monkeypatch.setenv("HINTERLAND_DATABASE_PORT", "6544")
    monkeypatch.setenv("HINTERLAND_DEV_AUTH_ENABLED", "true")
    monkeypatch.setenv("HINTERLAND_DEV_AUTH_TOKEN", "hinterland-dev-key")
    monkeypatch.setenv("HINTERLAND_KID_JWT_ISSUER", "https://api.thehinterlandguide.app")
    monkeypatch.setenv("HINTERLAND_KID_JWT_AUDIENCE", "hinterland-api")
    monkeypatch.setenv("HINTERLAND_KID_JWT_KID", "k1-2026-07")
    monkeypatch.setenv("HINTERLAND_ORGANISM_FALLBACK_PROVIDER", "azure_vision")

    settings = Settings()

    assert settings.env == "dev"
    assert settings.database_port == 6544
    assert settings.dev_login_enabled is True
    assert settings.dev_login_key == "hinterland-dev-key"
    assert settings.hinterland_jwt_issuer == "https://api.thehinterlandguide.app"
    assert settings.hinterland_jwt_audience == "hinterland-api"
    assert settings.hinterland_jwt_kid == "k1-2026-07"
    assert settings.organism_fallback_provider == "azure_vision"


def test_hinterland_settings_use_the_deployment_variable_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HINTERLAND_ENV", "dev")
    monkeypatch.setenv("HINTERLAND_DEV_AUTH_TOKEN", "hinterland-key")

    settings = Settings()

    assert settings.env == "dev"
    assert settings.dev_login_key == "hinterland-key"


def test_stub_auth_allowed_fails_closed_outside_local() -> None:
    assert Settings(env="local").stub_auth_allowed is True
    assert Settings(env="dev").stub_auth_allowed is False
    assert Settings(env="staging").stub_auth_allowed is False
    assert Settings(env="prod").stub_auth_allowed is False
    # Explicit override wins in either direction.
    assert Settings(env="prod", allow_stub_auth=True).stub_auth_allowed is True
    assert Settings(env="local", allow_stub_auth=False).stub_auth_allowed is False
