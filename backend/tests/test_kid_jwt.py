"""Unit tests for the Hinterland RS256 kid-JWT mint/verify path.

Tests are written against the planned ``app.core.kid_jwt`` module surface
described in the Phase 6a design plan:

* ``mint_handoff_token(*, kid_user_id, parent_id, group_id, settings) -> (str, str)``
* ``mint_session_token(*, kid_user_id, parent_id, group_id, settings) -> str``
* ``verify_hinterland_jwt(token, *, settings, expected_token_type=None) -> dict``
* ``public_jwks(settings) -> dict``
* ``InvalidHinterlandJwt`` exception

The signing/public PEMs are normally read from Azure Key Vault via the
``app.core.key_vault`` helpers; tests monkeypatch those helpers to return
a locally-generated RSA-2048 key pair so the round-trip path executes
PyJWT's real RS256 encode/decode without any cloud dependency.
"""

from __future__ import annotations

import importlib
from datetime import UTC, datetime, timedelta

import pytest

from app.core.config import Settings


def _kid_jwt_or_skip():
    """Import ``app.core.kid_jwt`` lazily; skip the suite if it isn't there."""
    try:
        return importlib.import_module("app.core.kid_jwt")
    except ModuleNotFoundError:
        pytest.skip("app.core.kid_jwt not present yet")


def _key_vault_or_skip():
    try:
        return importlib.import_module("app.core.key_vault")
    except ModuleNotFoundError:
        pytest.skip("app.core.key_vault not present yet")


def _generate_rsa_keypair() -> tuple[bytes, bytes]:
    """Generate a fresh RSA-2048 key pair and return (private_pem, public_pem)."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return private_pem, public_pem


@pytest.fixture
def settings() -> Settings:
    return Settings(env="local", app_version="test")


@pytest.fixture
def rsa_keypair() -> tuple[bytes, bytes]:
    return _generate_rsa_keypair()


@pytest.fixture
def patch_key_vault(
    monkeypatch: pytest.MonkeyPatch,
    rsa_keypair: tuple[bytes, bytes],
) -> tuple[bytes, bytes]:
    """Replace the Azure Key Vault lookups with in-memory PEM bytes."""
    kid_jwt = _kid_jwt_or_skip()
    key_vault = _key_vault_or_skip()

    private_pem, public_pem = rsa_keypair

    monkeypatch.setattr(key_vault, "get_kid_signing_pem", lambda settings: private_pem)
    monkeypatch.setattr(key_vault, "get_kid_public_pem", lambda settings: public_pem)
    # The kid_jwt module pulls these names into its own namespace at import
    # time; patch the bound references too if they exist.
    if hasattr(kid_jwt, "get_kid_signing_pem"):
        monkeypatch.setattr(kid_jwt, "get_kid_signing_pem", lambda settings: private_pem)
    if hasattr(kid_jwt, "get_kid_public_pem"):
        monkeypatch.setattr(kid_jwt, "get_kid_public_pem", lambda settings: public_pem)
    # Bust any process-local jwks cache the implementation may keep.
    if hasattr(kid_jwt, "public_jwks") and hasattr(kid_jwt.public_jwks, "cache_clear"):
        kid_jwt.public_jwks.cache_clear()
    return private_pem, public_pem


def test_mint_handoff_returns_token_and_jti(
    patch_key_vault: tuple[bytes, bytes],
    settings: Settings,
) -> None:
    kid_jwt = _kid_jwt_or_skip()
    token, jti = kid_jwt.mint_handoff_token(
        kid_user_id="01J0KIDID0000000000000ULID",
        parent_id="01J0PARENTID0000000000ULID",
        group_id="01J0GROUPID00000000000ULID",
        settings=settings,
    )
    assert isinstance(token, str) and token.count(".") == 2
    assert isinstance(jti, str) and len(jti) >= 16


def test_mint_handoff_token_contains_expected_claims(
    patch_key_vault: tuple[bytes, bytes],
    settings: Settings,
) -> None:
    import jwt as pyjwt

    kid_jwt = _kid_jwt_or_skip()
    token, jti = kid_jwt.mint_handoff_token(
        kid_user_id="01J0KIDID0000000000000ULID",
        parent_id="01J0PARENTID0000000000ULID",
        group_id="01J0GROUPID00000000000ULID",
        settings=settings,
    )
    decoded = pyjwt.decode(token, options={"verify_signature": False})

    assert decoded["sub"] == "01J0KIDID0000000000000ULID"
    assert decoded["parent_id"] == "01J0PARENTID0000000000ULID"
    assert decoded["group_id"] == "01J0GROUPID00000000000ULID"
    assert decoded["role"] == "kid"
    assert decoded["token_type"] == "handoff"
    assert decoded["jti"] == jti
    assert decoded["iss"] == settings.hinterland_jwt_issuer
    assert decoded["aud"] == settings.hinterland_jwt_audience
    # 15-minute handoff TTL by default.
    assert decoded["exp"] - decoded["iat"] == settings.hinterland_handoff_ttl_seconds

    header = pyjwt.get_unverified_header(token)
    assert header["alg"] == "RS256"
    assert header["kid"] == settings.hinterland_jwt_kid


def test_mint_session_token_uses_session_ttl(
    patch_key_vault: tuple[bytes, bytes],
    settings: Settings,
) -> None:
    import jwt as pyjwt

    kid_jwt = _kid_jwt_or_skip()
    token = kid_jwt.mint_session_token(
        kid_user_id="01J0KIDID0000000000000ULID",
        parent_id="01J0PARENTID0000000000ULID",
        group_id="01J0GROUPID00000000000ULID",
        settings=settings,
    )
    decoded = pyjwt.decode(token, options={"verify_signature": False})
    assert decoded["token_type"] == "session"
    assert decoded["exp"] - decoded["iat"] == settings.hinterland_session_ttl_seconds


def test_verify_round_trip_succeeds(
    patch_key_vault: tuple[bytes, bytes],
    settings: Settings,
) -> None:
    kid_jwt = _kid_jwt_or_skip()
    token, jti = kid_jwt.mint_handoff_token(
        kid_user_id="01J0KIDID0000000000000ULID",
        parent_id="01J0PARENTID0000000000ULID",
        group_id="01J0GROUPID00000000000ULID",
        settings=settings,
    )

    claims = kid_jwt.verify_hinterland_jwt(
        token,
        settings=settings,
        expected_token_type="handoff",
    )
    assert claims["sub"] == "01J0KIDID0000000000000ULID"
    assert claims["jti"] == jti
    assert claims["token_type"] == "handoff"


def test_verify_rejects_expired_token(
    patch_key_vault: tuple[bytes, bytes],
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An expired handoff JWT must raise InvalidHinterlandJwt."""
    kid_jwt = _kid_jwt_or_skip()

    # Force the TTL to a negative value so the minted token is born expired.
    monkeypatch.setattr(settings, "hinterland_handoff_ttl_seconds", -10)
    token, _ = kid_jwt.mint_handoff_token(
        kid_user_id="01J0KIDID0000000000000ULID",
        parent_id="01J0PARENTID0000000000ULID",
        group_id="01J0GROUPID00000000000ULID",
        settings=settings,
    )

    with pytest.raises(kid_jwt.InvalidHinterlandJwt):
        kid_jwt.verify_hinterland_jwt(token, settings=settings)


def test_verify_rejects_wrong_audience(
    patch_key_vault: tuple[bytes, bytes],
    settings: Settings,
) -> None:
    """A token whose aud claim doesn't match settings.hinterland_jwt_audience -> reject."""
    import jwt as pyjwt

    kid_jwt = _kid_jwt_or_skip()
    private_pem, _ = patch_key_vault
    now = datetime.now(UTC)
    payload = {
        "iss": settings.hinterland_jwt_issuer,
        "aud": "some-other-audience",
        "sub": "01J0KIDID0000000000000ULID",
        "jti": "01HANDOFFJTI00000000000000",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=15)).timestamp()),
        "token_type": "handoff",
        "role": "kid",
        "parent_id": "p1",
        "group_id": "g1",
    }
    token = pyjwt.encode(
        payload,
        private_pem,
        algorithm="RS256",
        headers={"kid": settings.hinterland_jwt_kid, "alg": "RS256", "typ": "JWT"},
    )

    with pytest.raises(kid_jwt.InvalidHinterlandJwt):
        kid_jwt.verify_hinterland_jwt(token, settings=settings)


def test_verify_rejects_wrong_issuer(
    patch_key_vault: tuple[bytes, bytes],
    settings: Settings,
) -> None:
    import jwt as pyjwt

    kid_jwt = _kid_jwt_or_skip()
    private_pem, _ = patch_key_vault
    now = datetime.now(UTC)
    payload = {
        "iss": "https://evil.example.com",
        "aud": settings.hinterland_jwt_audience,
        "sub": "01J0KIDID0000000000000ULID",
        "jti": "01HANDOFFJTI00000000000000",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=15)).timestamp()),
        "token_type": "handoff",
        "role": "kid",
        "parent_id": "p1",
        "group_id": "g1",
    }
    token = pyjwt.encode(
        payload,
        private_pem,
        algorithm="RS256",
        headers={"kid": settings.hinterland_jwt_kid, "alg": "RS256", "typ": "JWT"},
    )

    with pytest.raises(kid_jwt.InvalidHinterlandJwt):
        kid_jwt.verify_hinterland_jwt(token, settings=settings)


def test_verify_rejects_bad_signature(
    patch_key_vault: tuple[bytes, bytes],
    settings: Settings,
) -> None:
    """Tampering with the signature segment must make verification fail."""
    kid_jwt = _kid_jwt_or_skip()
    token, _ = kid_jwt.mint_handoff_token(
        kid_user_id="01J0KIDID0000000000000ULID",
        parent_id="01J0PARENTID0000000000ULID",
        group_id="01J0GROUPID00000000000ULID",
        settings=settings,
    )
    # Flip the last 8 chars of the signature to make it invalid but still
    # base64-decodable.
    header_payload, _signature = token.rsplit(".", 1)
    tampered = header_payload + ".AAAAAAAAAAAAAAAAAAAAAAAAAAAA"

    with pytest.raises(kid_jwt.InvalidHinterlandJwt):
        kid_jwt.verify_hinterland_jwt(tampered, settings=settings)


def test_verify_rejects_mismatched_token_type(
    patch_key_vault: tuple[bytes, bytes],
    settings: Settings,
) -> None:
    """A session token presented where a handoff is expected must be rejected."""
    kid_jwt = _kid_jwt_or_skip()
    session_token = kid_jwt.mint_session_token(
        kid_user_id="01J0KIDID0000000000000ULID",
        parent_id="01J0PARENTID0000000000ULID",
        group_id="01J0GROUPID00000000000ULID",
        settings=settings,
    )

    with pytest.raises(kid_jwt.InvalidHinterlandJwt):
        kid_jwt.verify_hinterland_jwt(
            session_token,
            settings=settings,
            expected_token_type="handoff",
        )


def test_public_jwks_returns_rsa_key(
    patch_key_vault: tuple[bytes, bytes],
    settings: Settings,
) -> None:
    kid_jwt = _kid_jwt_or_skip()
    jwks = kid_jwt.public_jwks(settings)
    assert "keys" in jwks
    assert len(jwks["keys"]) == 1
    key = jwks["keys"][0]
    assert key["kty"] == "RSA"
    assert key["alg"] == "RS256"
    assert key["use"] == "sig"
    assert key["kid"] == settings.hinterland_jwt_kid
    # Base64url-encoded modulus + exponent (RFC 7518).
    assert isinstance(key["n"], str) and len(key["n"]) > 0
    assert isinstance(key["e"], str) and len(key["e"]) > 0
