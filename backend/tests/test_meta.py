from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app


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
        "version": "test",
        "capabilities": {
            "observation": {"photo_helper_enabled": False},
            "groups": {"shared_groups_enabled": False},
        },
    }


def test_v1_meta_enables_photo_helper_only_after_every_gate() -> None:
    app = create_app(
        Settings(
            env="local",
            app_version="test",
            inat_cv_enabled=True,
            inat_cv_disclosure_approved=True,
            inat_cv_benchmark_approved=True,
            inat_oauth_token="configured-token",
            inat_base_url="https://api.inaturalist.org/v1",
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
            inat_oauth_token="",
        )
    )

    with TestClient(app) as test_client:
        response = test_client.get("/v1/meta")

    assert response.json()["capabilities"]["observation"] == {
        "photo_helper_enabled": False,
    }


def test_v1_meta_reports_only_the_global_shared_groups_rollout() -> None:
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
    assert response.json() == {
        "error": {
            "code": "not_found",
            "message": "Not Found",
            "request_id": "test-request-id",
            "details": None,
        }
    }
