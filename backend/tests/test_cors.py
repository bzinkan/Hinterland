from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

_TRUSTED_PARENT_ORIGINS = (
    "https://parents.thehinterlandguide.app",
    "https://purple-coast-088e6b30f.7.azurestaticapps.net",
)


@pytest.mark.parametrize("origin", _TRUSTED_PARENT_ORIGINS)
def test_parent_consent_preflight_allows_exact_trusted_origin(
    client: TestClient,
    origin: str,
) -> None:
    response = client.options(
        "/v1/auth/consent",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == origin
    assert response.headers["access-control-allow-credentials"] == "true"
    assert "Origin" in response.headers["vary"].split(", ")
    assert "POST" in response.headers["access-control-allow-methods"].split(", ")
    assert "content-type" in {
        value.strip().lower()
        for value in response.headers["access-control-allow-headers"].split(",")
    }


@pytest.mark.parametrize(
    "origin",
    (
        "https://thehinterlandguide.app",
        "https://evil.example",
        "null",
    ),
)
def test_parent_consent_preflight_rejects_untrusted_origin(
    client: TestClient,
    origin: str,
) -> None:
    response = client.options(
        "/v1/auth/consent",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )

    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers
