"""Tests for scripts/draft_expedition.py (author-time drafting tool).

The script lives at the repo root, outside backend/app (ADR 0002: LLM
tooling is author-time only and never imported by the backend). The
sys.path bootstrap below mirrors the script's own backend bootstrap so
the module is importable from the backend pytest rootdir.

The anthropic provider is exercised with a fake injected client only --
no SDK import, no network call.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from app.models.expedition import Expedition

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import draft_expedition  # noqa: E402


class _FakeBlock:
    type = "text"

    def __init__(self, text: str) -> None:
        self.text = text


class _FakeResponse:
    def __init__(self, text: str) -> None:
        self.content = [_FakeBlock(text)]


class _FakeMessages:
    def __init__(self, replies: list[str]) -> None:
        self._replies = list(replies)
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> _FakeResponse:
        self.calls.append(kwargs)
        return _FakeResponse(self._replies.pop(0))


class _FakeClient:
    """Stands in for anthropic.Anthropic(); returns canned replies in order."""

    def __init__(self, replies: list[str]) -> None:
        self.messages = _FakeMessages(replies)


def _args(*extra: str) -> argparse.Namespace:
    return draft_expedition.parse_args(["meadow bugs", *extra])


def _valid_model_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": "meadow_bugs",
        "title": "Meadow Bug Hunt",
        "subtitle": "Insects in the tall grass",
        "tier": 1,
        "duration_minutes": 25,
        "environments": ["park"],
        "intro": "Tall grass hides a whole insect city. Find its residents.",
        "outro": "You just contributed real data to science.",
        "prerequisites": [],
        "steps": [
            {
                "id": "any_insect",
                "description": "Find an insect in the grass",
                "match": {"kind": "iconic_taxon", "value": "Insecta"},
                "hint": "Look near flowers or on tall stems.",
            },
            {
                "id": "new_find",
                "description": "Find something not in your Dex",
                "match": {"kind": "not_in_dex"},
            },
        ],
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# Static provider
# ---------------------------------------------------------------------------


def test_static_provider_is_byte_deterministic(capsys: pytest.CaptureFixture[str]) -> None:
    argv = ["city park insects", "--environment", "park"]
    assert draft_expedition.main(argv) == 0
    first = capsys.readouterr().out
    assert draft_expedition.main(argv) == 0
    second = capsys.readouterr().out
    assert first == second
    Expedition.model_validate(json.loads(first))


def test_static_defaults_unchanged(capsys: pytest.CaptureFixture[str]) -> None:
    assert draft_expedition.main(["city park insects"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["id"] == "city_park_insects"
    assert data["title"] == "City Park Insects"
    assert data["tier"] == 1
    assert data["duration_minutes"] == 20
    assert data["environments"] == ["other"]
    assert [step["id"] for step in data["steps"]] == [
        "first_observation",
        "new_to_you",
        "different_spot",
    ]


# ---------------------------------------------------------------------------
# parse_model_json
# ---------------------------------------------------------------------------


def test_parse_model_json_clean() -> None:
    assert draft_expedition.parse_model_json('{"id": "x"}') == {"id": "x"}


def test_parse_model_json_strips_markdown_fences() -> None:
    fenced = '```json\n{"id": "x"}\n```'
    assert draft_expedition.parse_model_json(fenced) == {"id": "x"}


def test_parse_model_json_rejects_garbage() -> None:
    with pytest.raises(ValueError, match="not valid JSON"):
        draft_expedition.parse_model_json("here is your expedition!")


def test_parse_model_json_rejects_non_object() -> None:
    with pytest.raises(ValueError, match="single JSON object"):
        draft_expedition.parse_model_json("[1, 2]")


# ---------------------------------------------------------------------------
# Anthropic provider (fake injected client)
# ---------------------------------------------------------------------------


def test_generate_anthropic_valid_json_returns_expedition() -> None:
    client = _FakeClient([json.dumps(_valid_model_payload())])
    expedition = draft_expedition.generate_anthropic(client, _args("--provider", "anthropic"))
    assert isinstance(expedition, Expedition)
    assert expedition.id == "meadow_bugs"
    assert len(client.messages.calls) == 1
    call = client.messages.calls[0]
    assert call["model"] == draft_expedition.ANTHROPIC_MODEL
    assert call["max_tokens"] == draft_expedition.MAX_TOKENS


def test_generate_anthropic_retries_once_with_validation_error() -> None:
    bad = json.dumps(_valid_model_payload(tier=9))
    good = json.dumps(_valid_model_payload())
    client = _FakeClient([bad, good])
    expedition = draft_expedition.generate_anthropic(client, _args("--provider", "anthropic"))
    assert isinstance(expedition, Expedition)
    assert len(client.messages.calls) == 2
    retry_messages = client.messages.calls[1]["messages"]
    assert retry_messages[1]["role"] == "assistant"
    assert retry_messages[2]["role"] == "user"
    assert "tier" in retry_messages[2]["content"]


def test_generate_anthropic_invalid_twice_returns_none(
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = _FakeClient(["not json", "still not json"])
    expedition = draft_expedition.generate_anthropic(client, _args("--provider", "anthropic"))
    assert expedition is None
    assert len(client.messages.calls) == 2
    assert "failed validation twice" in capsys.readouterr().err


def test_main_falls_back_to_static_with_exit_zero(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    client = _FakeClient(["not json", "still not json"])
    monkeypatch.setattr(draft_expedition, "_create_client", lambda: client)

    assert draft_expedition.main(["city park insects", "--provider", "anthropic"]) == 0
    captured = capsys.readouterr()
    assert "falling back to the static template" in captured.err
    fallback = Expedition.model_validate(json.loads(captured.out))
    assert fallback.steps[0].id == "first_observation"
    assert len(client.messages.calls) == 2


def test_main_missing_api_key_fails_before_any_call(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    def _boom() -> Any:
        raise AssertionError("client must not be created without an API key")

    monkeypatch.setattr(draft_expedition, "_create_client", _boom)
    assert draft_expedition.main(["city park insects", "--provider", "anthropic"]) == 1
    assert "ANTHROPIC_API_KEY" in capsys.readouterr().err


def test_main_missing_sdk_reports_install_hint(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    def _import_error() -> Any:
        raise ImportError("No module named 'anthropic'")

    monkeypatch.setattr(draft_expedition, "_create_client", _import_error)
    assert draft_expedition.main(["city park insects", "--provider", "anthropic"]) == 1
    assert "pip install anthropic" in capsys.readouterr().err


def test_cli_overrides_win_over_model_output() -> None:
    payload = _valid_model_payload(
        id="model_id",
        title="Model Title",
        tier=2,
        duration_minutes=45,
        environments=["yard"],
    )
    client = _FakeClient([json.dumps(payload)])
    args = _args(
        "--provider",
        "anthropic",
        "--id",
        "flag_id",
        "--title",
        "Flag Title",
        "--tier",
        "3",
        "--duration-minutes",
        "30",
        "--environment",
        "park",
    )
    expedition = draft_expedition.generate_anthropic(client, args)
    assert expedition is not None
    assert expedition.id == "flag_id"
    assert expedition.title == "Flag Title"
    assert expedition.tier == 3
    assert expedition.duration_minutes == 30
    assert expedition.environments == ["park"]
