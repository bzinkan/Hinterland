from collections.abc import AsyncIterator, Iterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes.species import facts_from_payload
from app.core.config import Settings
from app.db import models
from app.db.session import get_db_session
from app.main import create_app
from tests.helpers.auth import stub_token_verifier

_FIREBASE_UID = "firebase-kid-001"
_USER_ID = "01J0KIDID0000000000000ULID"
_TAXON_ID = 12345

# A realistic /taxa/{id} result: the fields the facts endpoint reads,
# with the HTML-ish wikipedia_summary iNat actually returns. Existing
# species_cache fixtures only carry the minimal matcher fields; this one
# documents the facts contract.
_FULL_PAYLOAD: dict[str, object] = {
    "id": _TAXON_ID,
    "name": "Cardinalis cardinalis",
    "preferred_common_name": "Northern Cardinal",
    "iconic_taxon_name": "Aves",
    "rank": "species",
    "wikipedia_summary": (
        "The <i>northern cardinal</i> is a songbird &amp; year-round "
        "resident of eastern North America."
    ),
    "wikipedia_url": "https://en.wikipedia.org/wiki/Northern_cardinal",
    "observations_count": 2412345,
    "conservation_status": {"status": "LC", "status_name": "least concern"},
    "ancestor_ids": [48460, 1, 3],
}


def _stub_token_verifier(monkeypatch: pytest.MonkeyPatch) -> None:
    stub_token_verifier(
        monkeypatch, uid=_FIREBASE_UID, role="kid", group_id="01J0GROUPID00000000000ULID"
    )


def _build_client(fake_session: AsyncMock) -> Iterator[TestClient]:
    app = create_app(Settings(env="local", app_version="test"))

    async def override() -> AsyncIterator[AsyncSession]:
        yield fake_session

    app.dependency_overrides[get_db_session] = override
    with TestClient(app, raise_server_exceptions=True) as test_client:
        yield test_client


@pytest.fixture
def fake_session() -> AsyncMock:
    return AsyncMock(spec=AsyncSession)


@pytest.fixture
def species_client(fake_session: AsyncMock) -> Iterator[TestClient]:
    yield from _build_client(fake_session)


def _user_row() -> models.User:
    return models.User(
        id=_USER_ID,
        firebase_uid=_FIREBASE_UID,
        role="kid",
        display_name="Kid Name",
    )


def _wire_cache_hit(fake_session: AsyncMock, payload: dict[str, object]) -> None:
    """user select -> species_cache row select (get_source_payload)."""
    user_result = MagicMock()
    user_result.scalar_one_or_none = MagicMock(return_value=_user_row())

    cache_row = models.SpeciesCache(
        taxon_id=_TAXON_ID,
        scientific_name="Cardinalis cardinalis",
        common_name="Northern Cardinal",
        iconic_taxon="Aves",
        source_payload=payload,
    )
    cache_result = MagicMock()
    cache_result.scalar_one_or_none = MagicMock(return_value=cache_row)

    side_effects: list[Any] = [user_result, cache_result]
    fake_session.execute = AsyncMock(side_effect=side_effects)
    fake_session.commit = AsyncMock()


# ---------------------------------------------------------------------------
# Route behavior
# ---------------------------------------------------------------------------


def test_facts_require_bearer_token(species_client: TestClient) -> None:
    response = species_client.get(f"/v1/species/{_TAXON_ID}")
    assert response.status_code == 401


def test_facts_403_when_no_postgres_user(
    monkeypatch: pytest.MonkeyPatch,
    species_client: TestClient,
    fake_session: AsyncMock,
) -> None:
    _stub_token_verifier(monkeypatch)
    user_result = MagicMock()
    user_result.scalar_one_or_none = MagicMock(return_value=None)
    fake_session.execute = AsyncMock(return_value=user_result)

    response = species_client.get(
        f"/v1/species/{_TAXON_ID}",
        headers={"Authorization": "Bearer fake"},
    )
    assert response.status_code == 403


def test_facts_cache_hit_returns_stripped_summary(
    monkeypatch: pytest.MonkeyPatch,
    species_client: TestClient,
    fake_session: AsyncMock,
) -> None:
    _stub_token_verifier(monkeypatch)
    _wire_cache_hit(fake_session, _FULL_PAYLOAD)

    response = species_client.get(
        f"/v1/species/{_TAXON_ID}",
        headers={"Authorization": "Bearer fake"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["facts_available"] is True
    assert body["common_name"] == "Northern Cardinal"
    assert body["scientific_name"] == "Cardinalis cardinalis"
    assert body["rank"] == "species"
    assert body["iconic_taxon"] == "Aves"
    # HTML tags stripped, entities unescaped.
    assert body["summary"] == (
        "The northern cardinal is a songbird & year-round resident of eastern North America."
    )
    assert body["wikipedia_url"] == "https://en.wikipedia.org/wiki/Northern_cardinal"
    assert body["observations_worldwide"] == 2412345
    assert body["conservation_status"] == "least concern"


def test_facts_degrade_gracefully_when_catalog_has_no_source_facts(
    monkeypatch: pytest.MonkeyPatch,
    species_client: TestClient,
    fake_session: AsyncMock,
) -> None:
    """A minimal reviewed catalog row remains useful without live lookup."""
    _stub_token_verifier(monkeypatch)
    _wire_cache_hit(fake_session, {})

    response = species_client.get(
        f"/v1/species/{_TAXON_ID}",
        headers={"Authorization": "Bearer fake"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["facts_available"] is False
    assert body["summary"] is None
    assert body["common_name"] == "Northern Cardinal"


def test_facts_404_when_taxon_unknown(
    monkeypatch: pytest.MonkeyPatch,
    species_client: TestClient,
    fake_session: AsyncMock,
) -> None:
    _stub_token_verifier(monkeypatch)
    user_result = MagicMock()
    user_result.scalar_one_or_none = MagicMock(return_value=_user_row())
    missing_result = MagicMock()
    missing_result.scalar_one_or_none = MagicMock(return_value=None)
    fake_session.execute = AsyncMock(side_effect=[user_result, missing_result])

    response = species_client.get(
        f"/v1/species/{_TAXON_ID}",
        headers={"Authorization": "Bearer fake"},
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# facts_from_payload (pure)
# ---------------------------------------------------------------------------


def test_facts_from_payload_full() -> None:
    facts = facts_from_payload(_TAXON_ID, _FULL_PAYLOAD)
    assert facts.taxon_id == _TAXON_ID
    assert facts.common_name == "Northern Cardinal"
    assert facts.summary is not None and "<i>" not in facts.summary
    assert facts.summary is not None and "&" in facts.summary  # unescaped, not &amp;
    assert facts.observations_worldwide == 2412345
    assert facts.conservation_status == "least concern"
    assert facts.facts_available is True


def test_facts_from_payload_minimal_row_is_all_none() -> None:
    """Cache rows seeded by matcher tests carry only ancestor_ids."""
    facts = facts_from_payload(_TAXON_ID, {"ancestor_ids": [1, 2, 3]})
    assert facts.common_name is None
    assert facts.summary is None
    assert facts.observations_worldwide is None
    assert facts.conservation_status is None
    assert facts.facts_available is True


def test_facts_422_on_out_of_range_taxon_id(
    monkeypatch: pytest.MonkeyPatch,
    species_client: TestClient,
    fake_session: AsyncMock,
) -> None:
    """Unbounded ids overflow the asyncpg int4 bind -> must 422, not 500."""
    _stub_token_verifier(monkeypatch)
    response = species_client.get(
        "/v1/species/99999999999999",
        headers={"Authorization": "Bearer fake"},
    )
    assert response.status_code == 422
    response = species_client.get(
        "/v1/species/0",
        headers={"Authorization": "Bearer fake"},
    )
    assert response.status_code == 422


def test_facts_from_payload_never_synthesizes_markup() -> None:
    """Escaped markup must not survive the strip: a single
    strip-then-unescape pass would turn &lt;script&gt; into literal tags."""
    facts = facts_from_payload(
        _TAXON_ID,
        {"wikipedia_summary": "&lt;script&gt;alert(1)&lt;/script&gt;Cardinals sing."},
    )
    assert facts.summary is not None
    assert "<" not in facts.summary
    assert ">" not in facts.summary
    assert facts.summary == "alert(1)Cardinals sing."

    # Double-escaped input needs a second round; still no markup out.
    facts = facts_from_payload(
        _TAXON_ID,
        {"wikipedia_summary": "&amp;lt;b&amp;gt;bold claim&amp;lt;/b&amp;gt;"},
    )
    assert facts.summary is not None
    assert "<" not in facts.summary


def test_facts_from_payload_wikipedia_url_allowlist() -> None:
    def url_for(url: str) -> str | None:
        return facts_from_payload(_TAXON_ID, {"wikipedia_url": url}).wikipedia_url

    assert url_for("https://en.wikipedia.org/wiki/Northern_cardinal") is not None
    assert url_for("https://wikipedia.org/wiki/Bird") is not None
    assert url_for("http://en.wikipedia.org/wiki/Bird") is None  # not https
    assert url_for("https://evil.example.com/wiki/Bird") is None
    assert url_for("https://notwikipedia.org/wiki/Bird") is None
    assert url_for("javascript:alert(1)") is None


def test_facts_from_payload_rejects_malformed_fields() -> None:
    facts = facts_from_payload(
        _TAXON_ID,
        {
            "preferred_common_name": "",
            "observations_count": True,  # bools are ints; must be rejected
            "conservation_status": "endangered",  # wrong shape (str, not dict)
            "wikipedia_summary": "<p>   </p>",  # strips to empty -> None
        },
    )
    assert facts.common_name is None
    assert facts.observations_worldwide is None
    assert facts.conservation_status is None
    assert facts.summary is None
