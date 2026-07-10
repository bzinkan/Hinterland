"""Tests for admin/sync_expeditions.py.

`sync` is driven with a mocked AsyncSession (same style as the
sweep_stale_reviews tests); `load_and_validate` is driven with real
files under tmp_path. The regression that matters most here: an UPDATE
must never touch `archived` -- the old scripts/sync_expeditions.py
forced archived=False on every hash drift, silently resurrecting
tombstoned expeditions.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from admin.sync_expeditions import (
    ContentValidationError,
    _content_hash,
    load_and_validate,
    main,
    sync,
)
from app.db import models
from app.models.expedition import Expedition


def _exp_dict(exp_id: str = "backyard_starter", *, tier: int = 1) -> dict[str, Any]:
    return {
        "id": exp_id,
        "title": f"Test {exp_id}",
        "tier": tier,
        "duration_minutes": 20,
        "environments": ["yard"],
        "intro": "Find some things.",
        "outro": "Real science.",
        "steps": [
            {"id": "s0", "description": "x", "match": {"kind": "any_organism"}},
        ],
    }


def _validated(exp_id: str = "backyard_starter", *, tier: int = 1) -> tuple[Expedition, str]:
    exp = Expedition.model_validate(_exp_dict(exp_id, tier=tier))
    return exp, _content_hash(exp)


def _content_row(
    exp_id: str = "backyard_starter",
    *,
    tier: int = 1,
    content_hash: str = "stale",
    archived: bool = False,
) -> models.ExpeditionContent:
    # Column defaults don't fire on in-memory construction, so `archived`
    # is always set explicitly.
    return models.ExpeditionContent(
        id=exp_id,
        tier=tier,
        content_hash=content_hash,
        body={"id": exp_id},
        archived=archived,
    )


def _wire(fake_session: AsyncMock, *, existing: list[models.ExpeditionContent | None]) -> None:
    """Queue one SELECT result per expected `session.execute` call."""
    results = []
    for row in existing:
        result = MagicMock()
        result.scalar_one_or_none = MagicMock(return_value=row)
        results.append(result)
    fake_session.execute = AsyncMock(side_effect=results)
    fake_session.commit = AsyncMock()


@pytest.fixture
def fake_session() -> AsyncMock:
    return AsyncMock(spec=AsyncSession)


async def test_insert_path_sets_archived_false(fake_session: AsyncMock) -> None:
    exp, content_hash = _validated()
    _wire(fake_session, existing=[None])

    counts, unknown_ids = await sync(fake_session, [(exp, content_hash)])

    assert counts == {"inserted": 1, "updated": 0, "unchanged": 0, "unarchived": 0}
    assert unknown_ids == []
    added = fake_session.add.call_args.args[0]
    assert added.id == exp.id
    assert added.content_hash == content_hash
    assert added.archived is False
    fake_session.commit.assert_awaited_once()


async def test_update_path_preserves_archived(fake_session: AsyncMock) -> None:
    """Hash drift on a tombstoned expedition must NOT resurrect it."""
    exp, content_hash = _validated(tier=2)
    existing = _content_row(content_hash="stale", archived=True)
    _wire(fake_session, existing=[existing])

    counts, _ = await sync(fake_session, [(exp, content_hash)])

    assert counts == {"inserted": 0, "updated": 1, "unchanged": 0, "unarchived": 0}
    assert existing.tier == 2
    assert existing.content_hash == content_hash
    assert existing.body == exp.model_dump(mode="json")
    assert existing.archived is True  # the clobber regression
    fake_session.commit.assert_awaited_once()


async def test_unchanged_hash_is_a_noop(fake_session: AsyncMock) -> None:
    exp, content_hash = _validated()
    existing = _content_row(content_hash=content_hash, archived=False)
    stale_body = existing.body
    _wire(fake_session, existing=[existing])

    counts, _ = await sync(fake_session, [(exp, content_hash)])

    assert counts == {"inserted": 0, "updated": 0, "unchanged": 1, "unarchived": 0}
    assert existing.body is stale_body
    fake_session.add.assert_not_called()
    fake_session.commit.assert_awaited_once()


async def test_unarchive_sets_archived_false(fake_session: AsyncMock) -> None:
    existing = _content_row(archived=True)
    _wire(fake_session, existing=[existing])

    counts, unknown_ids = await sync(fake_session, [], unarchive_ids=["backyard_starter"])

    assert counts == {"inserted": 0, "updated": 0, "unchanged": 0, "unarchived": 1}
    assert unknown_ids == []
    assert existing.archived is False
    fake_session.commit.assert_awaited_once()


async def test_unarchive_unknown_id_reported_and_rest_still_processes(
    fake_session: AsyncMock,
) -> None:
    """An unknown --unarchive id doesn't stop the run: the remaining ids
    still process (and commit), and the unknown id is surfaced to main()
    which turns it into a nonzero exit (test below)."""
    existing = _content_row(archived=True)
    _wire(fake_session, existing=[None, existing])

    counts, unknown_ids = await sync(fake_session, [], unarchive_ids=["nope", "backyard_starter"])

    assert counts["unarchived"] == 1
    assert unknown_ids == ["nope"]
    assert existing.archived is False
    fake_session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_main_returns_nonzero_on_unknown_unarchive_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_session: AsyncMock
) -> None:
    """A typo'd --unarchive must flip the exit code AFTER the full sync
    ran -- an explicit operator action silently no-oping is how
    tombstones get "revived" without anyone noticing they typoed."""
    (tmp_path / "backyard_starter.json").write_text(json.dumps(_exp_dict()), encoding="utf-8")
    monkeypatch.setenv("HINTERLAND_CONTENT_ROOT", str(tmp_path))

    archived_row = _content_row("street_starter", archived=True)
    # Selects in sync order: backyard_starter content lookup (miss ->
    # insert), then the two --unarchive lookups ("nope" miss,
    # street_starter hit).
    _wire(fake_session, existing=[None, None, archived_row])

    class _SessionCtx:
        async def __aenter__(self) -> AsyncMock:
            return fake_session

        async def __aexit__(self, *exc_info: object) -> None:
            return None

    engine = MagicMock()
    engine.dispose = AsyncMock()
    monkeypatch.setattr("admin.sync_expeditions.create_async_engine", lambda url: engine)
    monkeypatch.setattr(
        "admin.sync_expeditions.async_sessionmaker",
        lambda *args, **kwargs: lambda: _SessionCtx(),
    )

    from app.core.config import get_settings

    get_settings.cache_clear()
    try:
        assert await main(["--unarchive", "nope", "--unarchive", "street_starter"]) == 1
    finally:
        get_settings.cache_clear()

    # The sync still processed everything before the nonzero exit: the
    # new content row was inserted, the real unarchive landed, and the
    # session committed.
    fake_session.add.assert_called_once()
    assert archived_row.archived is False
    fake_session.commit.assert_awaited_once()


async def test_dry_run_performs_no_writes(fake_session: AsyncMock) -> None:
    new_exp, new_hash = _validated("park_starter")
    drifted_exp, drifted_hash = _validated(tier=3)
    existing = _content_row(content_hash="stale", archived=True)
    archived_row = _content_row("street_starter", archived=True)
    _wire(fake_session, existing=[None, existing, archived_row])

    counts, unknown_ids = await sync(
        fake_session,
        [(new_exp, new_hash), (drifted_exp, drifted_hash)],
        unarchive_ids=["street_starter"],
        dry_run=True,
    )

    # Planned actions are still counted...
    assert counts == {"inserted": 1, "updated": 1, "unchanged": 0, "unarchived": 1}
    assert unknown_ids == []
    # ...but nothing was added, mutated, or committed.
    fake_session.add.assert_not_called()
    fake_session.commit.assert_not_called()
    assert existing.tier == 1
    assert existing.content_hash == "stale"
    assert existing.archived is True
    assert archived_row.archived is True


def test_load_and_validate_returns_hash_per_file(tmp_path: Path) -> None:
    (tmp_path / "backyard_starter.json").write_text(json.dumps(_exp_dict()), encoding="utf-8")
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "park_starter.json").write_text(
        json.dumps(_exp_dict("park_starter")), encoding="utf-8"
    )

    out = load_and_validate(tmp_path)

    assert [exp.id for exp, _ in out] == ["backyard_starter", "park_starter"]
    assert all(len(content_hash) == 64 for _, content_hash in out)


def test_validation_failure_aborts_before_any_write(tmp_path: Path) -> None:
    """One broken file poisons the whole run -- load_and_validate raises
    before the caller ever opens a session."""
    (tmp_path / "backyard_starter.json").write_text(json.dumps(_exp_dict()), encoding="utf-8")
    (tmp_path / "broken.json").write_text(
        json.dumps(_exp_dict("broken") | {"tier": 99}), encoding="utf-8"
    )

    with pytest.raises(ContentValidationError):
        load_and_validate(tmp_path)


def test_invalid_json_raises_content_validation_error(tmp_path: Path) -> None:
    (tmp_path / "garbage.json").write_text("{not json", encoding="utf-8")

    with pytest.raises(ContentValidationError):
        load_and_validate(tmp_path)


@pytest.mark.asyncio
async def test_missing_content_root_fails_loud(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A nonexistent content root is a packaging regression (bad env var, or
    the image stopped shipping content/expeditions) -- exit 1, never a green
    'nothing to sync' no-op."""
    monkeypatch.setenv("HINTERLAND_CONTENT_ROOT", str(tmp_path / "does-not-exist"))
    from app.core.config import get_settings

    get_settings.cache_clear()
    try:
        assert await main([]) == 1
    finally:
        get_settings.cache_clear()
