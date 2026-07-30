import base64
import json

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app


def _fresh_jwt() -> str:
    payload = base64.urlsafe_b64encode(json.dumps({"exp": 4_102_444_800}).encode())
    return f"header.{payload.decode().rstrip('=')}.signature"


class FakeReadyDatabase:
    async def readiness(self) -> tuple[str, str]:
        return "ok", "database connection succeeded"


class FakeBrokenDatabase:
    async def readiness(self) -> tuple[str, str]:
        return "not_ready", "connection refused"


def test_health_returns_ok(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body == {"status": "ok", "env": "local", "version": "test"}
    assert "x-request-id" in response.headers
    assert response.headers["x-hinterland-release-lane"] == "local"


def test_ready_returns_runtime_checks(client: TestClient) -> None:
    response = client.get("/ready")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["env"] == "local"
    assert {"name": "settings", "status": "ok", "detail": None} in body["checks"]
    assert any(
        check["name"] == "database" and check["status"] == "skipped" for check in body["checks"]
    )


def test_ready_enforces_database_when_required() -> None:
    app = create_app(
        Settings(
            env="local",
            app_version="test",
            readiness_database_required=True,
        )
    )
    app.state.database = FakeReadyDatabase()

    with TestClient(app) as test_client:
        response = test_client.get("/ready")

    assert response.status_code == 200
    assert {"name": "database", "status": "ok", "detail": "database connection succeeded"} in (
        response.json()["checks"]
    )


def test_ready_returns_503_when_database_is_not_ready() -> None:
    app = create_app(
        Settings(
            env="local",
            app_version="test",
            readiness_database_required=True,
        )
    )
    app.state.database = FakeBrokenDatabase()

    with TestClient(app) as test_client:
        response = test_client.get("/ready")

    assert response.status_code == 503
    assert response.json()["status"] == "not_ready"
    assert {"name": "database", "status": "not_ready", "detail": "connection refused"} in (
        response.json()["checks"]
    )


def test_v1_meta_returns_versioned_api_metadata(client: TestClient) -> None:
    response = client.get("/v1/meta")

    assert response.status_code == 200
    assert response.json() == {
        "name": "Hinterland API",
        "env": "local",
        "release_lane": "local",
        "version": "test",
        "capabilities": {
            "observation": {"photo_helper_enabled": False},
            "groups": {"shared_groups_enabled": False},
            "integrations": {
                "content_safety_enabled": False,
                "cv_qualification_enabled": False,
                "public_inat_submission_enabled": False,
            },
        },
    }


def test_v1_meta_enables_photo_helper_only_after_every_gate() -> None:
    app = create_app(
        Settings(
            env="local",
            app_version="test",
            release_lane="adult_qa",
            inat_cv_enabled=True,
            inat_cv_disclosure_approved=True,
            inat_cv_benchmark_approved=True,
            inat_cv_token=_fresh_jwt(),
            inat_base_url="https://api.inaturalist.org/v1",
            moderation_provider="azure_content_safety",
            content_safety_endpoint="https://content-safety.example.test",
            content_safety_key="configured-key",
        )
    )

    with TestClient(app) as test_client:
        response = test_client.get("/v1/meta")

    assert response.status_code == 200
    assert response.json()["capabilities"]["observation"] == {
        "photo_helper_enabled": True,
    }


def test_v1_meta_keeps_photo_helper_disabled_without_provider_credentials() -> None:
    app = create_app(
        Settings(
            env="local",
            app_version="test",
            inat_cv_enabled=True,
            inat_cv_disclosure_approved=True,
            inat_cv_benchmark_approved=True,
            inat_cv_token="",
        )
    )

    with TestClient(app) as test_client:
        response = test_client.get("/v1/meta")

    assert response.json()["capabilities"]["observation"] == {
        "photo_helper_enabled": False,
    }


def test_release_lane_header_is_exposed_to_parent_web(client: TestClient) -> None:
    response = client.get(
        "/v1/meta",
        headers={"Origin": "https://parents.thehinterlandguide.app"},
    )

    assert response.headers["x-hinterland-release-lane"] == "local"
    exposed = response.headers["access-control-expose-headers"].lower()
    assert "x-hinterland-release-lane" in exposed


def test_meta_and_header_report_the_same_configured_release_lane() -> None:
    app = create_app(Settings(env="local", app_version="test", release_lane="adult_qa"))

    with TestClient(app) as test_client:
        response = test_client.get("/v1/meta")

    assert response.json()["release_lane"] == "adult_qa"
    assert response.headers["x-hinterland-release-lane"] == "adult_qa"


def test_v1_meta_keeps_photo_helper_disabled_without_real_content_safety() -> None:
    app = create_app(
        Settings(
            env="local",
            app_version="test",
            inat_cv_enabled=True,
            inat_cv_disclosure_approved=True,
            inat_cv_benchmark_approved=True,
            inat_cv_token=_fresh_jwt(),
            moderation_provider="noop",
        )
    )

    with TestClient(app) as test_client:
        response = test_client.get("/v1/meta")

    assert response.json()["capabilities"]["observation"] == {
        "photo_helper_enabled": False,
    }


def test_v1_meta_reports_integration_egress_gates() -> None:
    app = create_app(
        Settings(
            env="local",
            app_version="test",
            release_lane="adult_qa",
            moderation_provider="azure_content_safety",
            content_safety_endpoint="https://content-safety.example.test",
            content_safety_key="configured-key",
            inat_submit_enabled=True,
            inat_submit_token="dedicated-submit-token",
            inat_base_url="https://api.inaturalist.org/v1",
        )
    )

    with TestClient(app) as test_client:
        response = test_client.get("/v1/meta")

    assert response.json()["capabilities"]["integrations"] == {
        "content_safety_enabled": True,
        "cv_qualification_enabled": False,
        "public_inat_submission_enabled": True,
    }


def test_v1_meta_reports_an_explicit_shared_groups_canary_rollout() -> None:
    app = create_app(
        Settings(
            env="local",
            app_version="test",
            shared_groups_enabled=True,
            shared_groups_canary_group_ids=["01CANARYGROUP0000000000000"],
        )
    )

    with TestClient(app) as test_client:
        response = test_client.get("/v1/meta")

    assert response.json()["capabilities"]["groups"] == {
        "shared_groups_enabled": True,
    }


def test_not_found_uses_error_envelope(client: TestClient) -> None:
    response = client.get("/v1/missing", headers={"x-request-id": "test-request-id"})

    assert response.status_code == 404
    assert response.headers["x-request-id"] == "test-request-id"
    assert response.headers["x-hinterland-release-lane"] == "local"
    assert response.json() == {
        "error": {
            "code": "not_found",
            "message": "Not Found",
            "request_id": "test-request-id",
            "details": None,
        }
    }
