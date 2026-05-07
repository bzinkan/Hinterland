from fastapi.testclient import TestClient


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


def test_v1_meta_returns_versioned_api_metadata(client: TestClient) -> None:
    response = client.get("/v1/meta")

    assert response.status_code == 200
    assert response.json() == {
        "name": "Dragonfly API",
        "env": "local",
        "version": "test",
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
