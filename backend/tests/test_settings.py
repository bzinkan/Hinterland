import base64
import json
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.core.config import Settings


def _jwt_with_exp(expiry: object) -> str:
    payload = base64.urlsafe_b64encode(json.dumps({"exp": expiry}).encode()).decode().rstrip("=")
    return f"header.{payload}.signature"


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


def test_release_lane_defaults_to_local_when_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HINTERLAND_RELEASE_LANE", raising=False)
    assert Settings().release_lane == "local"


@pytest.mark.parametrize("lane", ["local", "w1_internal", "adult_qa", "production"])
def test_release_lane_accepts_only_named_contracts(
    monkeypatch: pytest.MonkeyPatch,
    lane: str,
) -> None:
    monkeypatch.setenv("HINTERLAND_RELEASE_LANE", lane)
    assert Settings().release_lane == lane


def test_release_lane_rejects_malformed_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HINTERLAND_RELEASE_LANE", "adult-qa")
    with pytest.raises(ValidationError):
        Settings()


def test_default_cors_origins_are_exact_and_parent_only() -> None:
    assert Settings().cors_origins == [
        "http://localhost:19006",
        "https://parents.thehinterlandguide.app",
        "https://purple-coast-088e6b30f.7.azurestaticapps.net",
    ]
    assert "*" not in Settings().cors_origins


def test_shared_groups_are_default_deny_with_explicit_canary_support() -> None:
    settings = Settings()
    assert settings.shared_groups_enabled is False
    assert settings.shared_groups_allowed_for("group-a") is False

    canary = Settings(shared_groups_canary_group_ids=["group-a"])
    assert canary.shared_groups_allowed_for("group-a") is True
    assert canary.shared_groups_allowed_for("group-b") is False

    enabled = Settings(shared_groups_enabled=True)
    assert enabled.shared_groups_allowed_for("group-b") is True


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


@pytest.mark.parametrize(
    ("token", "expected"),
    [
        ("opaque", False),
        ("a.b.c", False),
        (_jwt_with_exp("4102444800"), False),
        (_jwt_with_exp(2_000), False),
        (_jwt_with_exp(2_801), True),
    ],
)
def test_cv_token_freshness_fails_closed(token: str, expected: bool) -> None:
    settings = Settings(
        inat_cv_token=token,
        inat_cv_token_expiry_safety_seconds=1800,
    )
    now = datetime.fromtimestamp(1_000, tz=UTC)
    assert settings.inat_cv_token_is_fresh(now=now) is expected


def test_inat_cv_and_submit_credentials_must_be_distinct() -> None:
    with pytest.raises(ValidationError, match="must be distinct"):
        Settings(inat_cv_token="same-secret", inat_submit_token="same-secret")


def test_retired_shared_inat_token_cannot_enable_either_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HINTERLAND_INAT_OAUTH_TOKEN", _jwt_with_exp(4_102_444_800))
    settings = Settings(
        inat_cv_enabled=True,
        inat_cv_disclosure_approved=True,
        inat_cv_benchmark_approved=True,
        inat_submit_enabled=True,
        moderation_provider="azure_content_safety",
        content_safety_endpoint="https://content-safety.example.test",
        content_safety_key="configured-key",
    )

    assert settings.inat_cv_token == ""
    assert settings.inat_submit_token == ""
    assert settings.photo_helper_enabled is False
    assert settings.inat_submit_egress_allowed is False


def test_public_inat_submission_is_default_deny_even_with_a_token() -> None:
    settings = Settings(inat_submit_token="submit-only")
    assert settings.inat_submit_enabled is False
    assert settings.inat_submit_egress_allowed is False


def test_adult_qa_uses_a_distinct_provider_disclosure_version() -> None:
    w1 = Settings(release_lane="w1_internal")
    qa = Settings(release_lane="adult_qa")

    assert w1.parent_consent_policy_version == "2026-07-11-W1-INTERNAL"
    assert qa.parent_consent_policy_version == "2026-07-30-ADULT-QA-EGRESS-V1"
    assert qa.parent_consent_policy_version != w1.parent_consent_policy_version


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
