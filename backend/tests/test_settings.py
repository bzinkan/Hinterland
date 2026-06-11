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


def test_stub_auth_allowed_fails_closed_outside_local() -> None:
    assert Settings(env="local").stub_auth_allowed is True
    assert Settings(env="dev").stub_auth_allowed is False
    assert Settings(env="staging").stub_auth_allowed is False
    assert Settings(env="prod").stub_auth_allowed is False
    # Explicit override wins in either direction.
    assert Settings(env="prod", allow_stub_auth=True).stub_auth_allowed is True
    assert Settings(env="local", allow_stub_auth=False).stub_auth_allowed is False
