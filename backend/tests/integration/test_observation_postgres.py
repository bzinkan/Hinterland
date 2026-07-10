"""Opt-in real-PostgreSQL verification for the Observation W1 contract.

Set ``OBSERVATION_TEST_DATABASE_URL`` to a *disposable* database whose name
contains ``test`` or ``verify``. The companion PowerShell script creates a
temporary Postgres 16 container, migrates it through Alembic head, runs this
module, and removes the container.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import math
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from time import perf_counter
from typing import ClassVar
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from PIL import Image
from sqlalchemy import func, select, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from ulid import ULID

from admin.dispatcher_replay import replay as replay_dispatcher
from admin.moderation_consumer import process_one as process_moderation_message
from admin.observation_health_probe import probe as probe_observation_health
from admin.observation_legacy_reconcile import reconcile_legacy_pending
from admin.sweep_stale_reviews import sweep
from app.api.routes.review_queue import _load_review_for_resolution
from app.core.config import Settings
from app.core.storage import StorageObjectProperties
from app.db import models
from app.db.session import get_db_session
from app.derived_state.rebuild import (
    acquire_user_lock,
    enqueue_rebuild,
    process_rebuild_job,
    rebuild_user_state,
)
from app.dispatcher.core import dispatch
from app.dispatcher.handlers.dex import DexHandler
from app.dispatcher.handlers.expedition import ExpeditionHandler
from app.dispatcher.registry import HANDLERS
from app.dispatcher.types import Context, HandlerResult, Reward
from app.main import create_app
from app.moderation.review_service import ReviewResolutionConflict, reject_review_item
from app.moderation.revocation import PhotoRevocationPending, revoke_and_reject_review_item
from tests.helpers.auth import stub_token_verifier

pytestmark = [pytest.mark.integration, pytest.mark.postgres]

_DATABASE_ENV = "OBSERVATION_TEST_DATABASE_URL"
_EXPECTED_ALEMBIC_HEAD = "20260710_0017"
_CANONICAL_BYTES = b"x" * 1024


@dataclass(frozen=True)
class PgHarness:
    engine: AsyncEngine
    sessions: async_sessionmaker[AsyncSession]


@dataclass(frozen=True)
class IdentityRows:
    parent_id: str
    user_id: str
    group_id: str
    membership_id: str


@dataclass(frozen=True)
class ObservationRows:
    identity: IdentityRows
    photo_id: str
    observation_id: str
    submission_key: str


@dataclass(frozen=True)
class ReviewRows:
    observation: ObservationRows
    review_id: str


class RevocationStorage:
    """Small verified-object store for real-Postgres revocation tests."""

    def __init__(self, objects: dict[str, bytes], *, fail_copy: bool = False) -> None:
        self.objects = dict(objects)
        self.fail_copy = fail_copy

    def get_object_properties(
        self,
        *,
        bucket: str,
        object_name: str,
    ) -> StorageObjectProperties:
        del bucket
        try:
            value = self.objects[object_name]
        except KeyError as exc:
            raise FileNotFoundError(object_name) from exc
        return StorageObjectProperties(len(value), "image/jpeg", "etag")

    def fetch_object_bytes(self, *, bucket: str, object_name: str) -> bytes:
        del bucket
        try:
            return self.objects[object_name]
        except KeyError as exc:
            raise FileNotFoundError(object_name) from exc

    def copy_object(
        self,
        *,
        src_bucket: str,
        src_object: str,
        dst_bucket: str,
        dst_object: str,
        expected_size: int | None = None,
        expected_sha256: str | None = None,
    ) -> None:
        del src_bucket, dst_bucket
        if self.fail_copy:
            raise RuntimeError("injected copy failure")
        value = self.objects[src_object]
        assert expected_size is None or len(value) == expected_size
        assert expected_sha256 is None or hashlib.sha256(value).hexdigest() == expected_sha256
        existing = self.objects.get(dst_object)
        assert existing is None or existing == value
        self.objects[dst_object] = value

    def delete_object(self, *, bucket: str, object_name: str) -> None:
        del bucket
        self.objects.pop(object_name, None)


class FinalizationStorage:
    """In-memory JPEG store for the real-PostgreSQL create route."""

    def __init__(self, *, object_name: str, image_bytes: bytes) -> None:
        self.objects = {object_name: image_bytes}

    def get_object_properties(
        self,
        *,
        bucket: str,
        object_name: str,
    ) -> StorageObjectProperties:
        del bucket
        try:
            value = self.objects[object_name]
        except KeyError as exc:
            raise FileNotFoundError(object_name) from exc
        return StorageObjectProperties(len(value), "image/jpeg", hashlib.sha256(value).hexdigest())

    def fetch_object_bytes(self, *, bucket: str, object_name: str) -> bytes:
        del bucket
        try:
            return self.objects[object_name]
        except KeyError as exc:
            raise FileNotFoundError(object_name) from exc

    def put_object_bytes(
        self,
        *,
        bucket: str,
        object_name: str,
        data: bytes,
        content_type: str,
        metadata: dict[str, str],
        overwrite: bool,
        expected_sha256: str | None = None,
    ) -> None:
        del bucket, content_type, metadata
        assert not overwrite
        assert object_name not in self.objects
        assert expected_sha256 is None or hashlib.sha256(data).hexdigest() == expected_sha256
        self.objects[object_name] = data

    def delete_object(self, *, bucket: str, object_name: str) -> None:
        del bucket
        self.objects.pop(object_name, None)


@pytest.fixture
async def pg_harness() -> PgHarness:
    raw_url = os.getenv(_DATABASE_ENV)
    if not raw_url:
        pytest.skip(f"set {_DATABASE_ENV} to a disposable migrated PostgreSQL database")

    url = make_url(raw_url)
    database_name = url.database or ""
    if not any(token in database_name.lower() for token in ("test", "verify")):
        pytest.fail(
            f"refusing to truncate database {database_name!r}; its name must contain test or verify"
        )
    if url.drivername == "postgresql":
        url = url.set(drivername="postgresql+asyncpg")
    if url.drivername != "postgresql+asyncpg":
        pytest.fail(f"{_DATABASE_ENV} must use PostgreSQL, got {url.drivername!r}")

    engine = create_async_engine(url, pool_size=10, max_overflow=5, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)

    try:
        async with engine.begin() as connection:
            table_names = (
                (
                    await connection.execute(
                        text(
                            "SELECT tablename FROM pg_tables "
                            "WHERE schemaname = 'public' AND tablename != 'alembic_version'"
                        )
                    )
                )
                .scalars()
                .all()
            )
            if table_names:
                quoted = ", ".join(f'"{name}"' for name in table_names)
                await connection.execute(text(f"TRUNCATE TABLE {quoted} RESTART IDENTITY CASCADE"))
        yield PgHarness(engine=engine, sessions=sessions)
    finally:
        await engine.dispose()


def _new_id() -> str:
    return str(ULID())


async def _seed_identity(session: AsyncSession) -> IdentityRows:
    parent_id = _new_id()
    user_id = _new_id()
    group_id = _new_id()
    membership_id = _new_id()
    session.add(
        models.User(
            id=parent_id,
            firebase_uid=f"parent-{parent_id}",
            role="parent",
            display_name="Test Parent",
        )
    )
    await session.commit()
    session.add(
        models.User(
            id=user_id,
            firebase_uid=f"kid-{user_id}",
            role="kid",
            display_name="Test Kid",
            parent_user_id=parent_id,
        )
    )
    await session.commit()
    session.add(
        models.Group(
            id=group_id,
            name="Postgres Verification",
            join_code=group_id[-6:],
            owner_user_id=parent_id,
        )
    )
    await session.commit()
    session.add(
        models.Membership(
            id=membership_id,
            group_id=group_id,
            user_id=user_id,
            role="kid",
            observation_count=0,
            dex_count=0,
        )
    )
    await session.commit()
    return IdentityRows(
        parent_id=parent_id,
        user_id=user_id,
        group_id=group_id,
        membership_id=membership_id,
    )


async def _add_photo(
    session: AsyncSession,
    identity: IdentityRows,
    *,
    submission_key: str | None = None,
) -> tuple[str, str]:
    photo_id = _new_id()
    key = submission_key or _new_id()
    session.add(
        models.Photo(
            id=photo_id,
            user_id=identity.user_id,
            bucket="observation-verification",
            object_name=f"pending/finalized/{photo_id}.jpg",
            canonical_object_name=f"pending/finalized/{photo_id}.jpg",
            status="pending",
            attachment_status="attached",
            submission_key=key,
            content_type="image/jpeg",
            byte_count=1024,
            width_px=100,
            height_px=100,
            sha256=hashlib.sha256(_CANONICAL_BYTES).hexdigest(),
            verified_at=datetime.now(UTC),
        )
    )
    await session.commit()
    return photo_id, key


def _observation(
    identity: IdentityRows,
    *,
    photo_id: str,
    submission_key: str,
    observation_id: str | None = None,
    taxon_id: int | None = 3,
    observed_at: datetime | None = None,
) -> models.Observation:
    return models.Observation(
        id=observation_id or _new_id(),
        user_id=identity.user_id,
        group_id=identity.group_id,
        photo_id=photo_id,
        submission_key=submission_key,
        taxon_id=taxon_id,
        species_name="Birds" if taxon_id is not None else None,
        latitude=None,
        longitude=None,
        geohash4=None,
        observed_at=observed_at or datetime.now(UTC),
        location_source="none",
        identification_source="catalog" if taxon_id is not None else "unknown",
        dispatch_status="pending",
        moderation_status="pilot_private",
        moderation_source="noop",
        rewards=[],
    )


async def _seed_observation(
    session: AsyncSession,
    *,
    taxon_id: int | None = 3,
) -> ObservationRows:
    identity = await _seed_identity(session)
    photo_id, submission_key = await _add_photo(session, identity)
    observation = _observation(
        identity,
        photo_id=photo_id,
        submission_key=submission_key,
        taxon_id=taxon_id,
    )
    session.add(observation)
    await session.execute(
        update(models.Membership)
        .where(models.Membership.id == identity.membership_id)
        .values(observation_count=1)
    )
    await session.commit()
    return ObservationRows(
        identity=identity,
        photo_id=photo_id,
        observation_id=observation.id,
        submission_key=submission_key,
    )


async def _add_observation_for_identity(
    session: AsyncSession,
    identity: IdentityRows,
    *,
    taxon_id: int | None = 3,
    observed_at: datetime | None = None,
) -> ObservationRows:
    photo_id, submission_key = await _add_photo(session, identity)
    observation = _observation(
        identity,
        photo_id=photo_id,
        submission_key=submission_key,
        taxon_id=taxon_id,
        observed_at=observed_at,
    )
    session.add(observation)
    await session.execute(
        update(models.Membership)
        .where(models.Membership.id == identity.membership_id)
        .values(observation_count=models.Membership.observation_count + 1)
    )
    await session.commit()
    return ObservationRows(
        identity=identity,
        photo_id=photo_id,
        observation_id=observation.id,
        submission_key=submission_key,
    )


async def _seed_quarantine_review(session: AsyncSession) -> ReviewRows:
    identity = await _seed_identity(session)
    session.add(
        models.Membership(
            id=_new_id(),
            group_id=identity.group_id,
            user_id=identity.parent_id,
            role="parent",
            observation_count=0,
            dex_count=0,
        )
    )
    await session.commit()

    observation_rows = await _add_observation_for_identity(session, identity)
    photo = await session.get(models.Photo, observation_rows.photo_id)
    observation = await session.get(models.Observation, observation_rows.observation_id)
    assert photo is not None
    assert observation is not None
    photo.status = "quarantine"
    photo.object_name = f"quarantine/{photo.id}.jpg"
    photo.canonical_object_name = photo.object_name
    observation.moderation_status = "quarantine"
    observation.moderation_source = "azure"
    await session.commit()

    review_id = _new_id()
    session.add(
        models.ReviewQueueItem(
            id=review_id,
            group_id=identity.group_id,
            photo_id=photo.id,
            observation_id=observation.id,
            status="pending",
            reason='{"sexual": 4}',
            created_at=datetime.now(UTC) - timedelta(days=45),
        )
    )
    await session.commit()
    return ReviewRows(observation=observation_rows, review_id=review_id)


async def _context(session: AsyncSession, rows: ObservationRows) -> Context:
    user = await session.get(models.User, rows.identity.user_id)
    group = await session.get(models.Group, rows.identity.group_id)
    observation = await session.get(models.Observation, rows.observation_id)
    photo = await session.get(models.Photo, rows.photo_id)
    assert user is not None
    assert group is not None
    assert observation is not None
    assert photo is not None
    return Context(
        db=session,
        user=user,
        group=group,
        observation=observation,
        photo=photo,
    )


async def test_migrations_reach_head_with_observation_constraints(pg_harness: PgHarness) -> None:
    async with pg_harness.engine.connect() as connection:
        version = await connection.scalar(text("SELECT version_num FROM alembic_version"))
        postgres_version = await connection.scalar(text("SHOW server_version"))
        constraints = set(
            (
                await connection.execute(
                    text("SELECT conname FROM pg_constraint WHERE conname = ANY(:names)"),
                    {
                        "names": [
                            "ck_memberships_observation_count",
                            "ck_memberships_dex_count",
                            "uq_photos_user_submission",
                            "uq_observations_photo_id",
                            "uq_observations_user_submission",
                            "observation_idempotency_pkey",
                            "expedition_observation_contributions_pkey",
                            "ck_dex_entries_observation_count",
                            "fk_dex_entries_representative_observation_id_observations",
                            "fk_dex_entries_representative_photo_id_photos",
                            "photo_revocations_pkey",
                            "ck_photo_revocations_state",
                            "ck_photo_revocations_attempt_count",
                            "uq_photo_revocations_review_id",
                        ]
                    },
                )
            ).scalars()
        )
        submission_key_nullability = dict(
            (
                await connection.execute(
                    text(
                        "SELECT table_name, is_nullable "
                        "FROM information_schema.columns "
                        "WHERE table_schema = 'public' "
                        "AND column_name = 'submission_key' "
                        "AND table_name IN ('photos', 'observations')"
                    )
                )
            ).all()
        )
        journal_indexes = set(
            (
                await connection.execute(
                    text(
                        "SELECT indexname FROM pg_indexes "
                        "WHERE schemaname = 'public' AND indexname = ANY(:names)"
                    ),
                    {
                        "names": [
                            "ix_dex_entries_user_first_seen",
                            "ix_observations_user_observed_active",
                        ]
                    },
                )
            ).scalars()
        )

    assert version == _EXPECTED_ALEMBIC_HEAD
    assert str(postgres_version).startswith("16.")
    assert constraints == {
        "ck_memberships_observation_count",
        "ck_memberships_dex_count",
        "uq_photos_user_submission",
        "uq_observations_photo_id",
        "uq_observations_user_submission",
        "observation_idempotency_pkey",
        "expedition_observation_contributions_pkey",
        "ck_dex_entries_observation_count",
        "fk_dex_entries_representative_observation_id_observations",
        "fk_dex_entries_representative_photo_id_photos",
        "photo_revocations_pkey",
        "ck_photo_revocations_state",
        "ck_photo_revocations_attempt_count",
        "uq_photo_revocations_review_id",
    }
    assert journal_indexes == {
        "ix_dex_entries_user_first_seen",
        "ix_observations_user_observed_active",
    }
    # Migrations run before the API deployment. Keep these additive columns
    # nullable for the one-release compatibility window so the old API can
    # continue inserting rows between migration and rollout.
    assert submission_key_nullability == {"photos": "YES", "observations": "YES"}


async def test_observation_health_probe_compiles_against_current_schema(
    pg_harness: PgHarness,
) -> None:
    async with pg_harness.sessions() as session:
        health = await probe_observation_health(session)
    assert health.healthy


async def test_create_route_flushes_observation_before_fk_ledgers(
    pg_harness: PgHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real create route commits Observation before its scalar-FK work rows."""

    photo_id = _new_id()
    submission_key = _new_id()
    raw_object = f"pending/uploads/{photo_id}.jpg"
    image_buffer = io.BytesIO()
    Image.new("RGB", (80, 60), (20, 90, 40)).save(image_buffer, format="JPEG")
    storage = FinalizationStorage(object_name=raw_object, image_bytes=image_buffer.getvalue())

    async with pg_harness.sessions() as session:
        identity = await _seed_identity(session)
        session.add(
            models.Photo(
                id=photo_id,
                user_id=identity.user_id,
                bucket="observation-verification",
                object_name=raw_object,
                status="pending",
                attachment_status="reserved",
                submission_key=submission_key,
                content_type="image/jpeg",
            )
        )
        await session.commit()

    app = create_app(
        Settings(
            env="local",
            app_version="test",
            observation_idempotency_required=True,
        )
    )
    app.state.signed_url_generator = storage

    async def override_session() -> AsyncIterator[AsyncSession]:
        async with pg_harness.sessions() as session:
            yield session

    app.dependency_overrides[get_db_session] = override_session
    stub_token_verifier(
        monkeypatch,
        uid=identity.user_id,
        role="kid",
        group_id=identity.group_id,
    )

    async def no_dispatch(_ctx: Context, _handlers: object) -> list[Reward]:
        return []

    monkeypatch.setattr("app.api.routes.observations.dispatch", no_dispatch)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://observation.test",
    ) as client:
        response = await client.post(
            "/v1/observations",
            headers={
                "Authorization": "Bearer test-kid",
                "Idempotency-Key": submission_key,
            },
            json={
                "photo_id": photo_id,
                "location_source": "none",
                "identification_source": "unknown",
            },
        )

    assert response.status_code == 201, response.text
    observation_id = response.json()["id"]
    async with pg_harness.sessions() as session:
        observation = await session.get(models.Observation, observation_id)
        outbox = await session.get(models.ModerationOutbox, observation_id)
        photo = await session.get(models.Photo, photo_id)
        membership = await session.get(models.Membership, identity.membership_id)
        handler_count = await session.scalar(
            select(func.count())
            .select_from(models.ObservationHandlerRun)
            .where(models.ObservationHandlerRun.observation_id == observation_id)
        )

    assert observation is not None
    assert outbox is not None and outbox.status == "pending"
    assert photo is not None and photo.attachment_status == "attached"
    assert membership is not None and membership.observation_count == 1
    assert handler_count == len(HANDLERS)
    assert raw_object not in storage.objects
    assert f"pending/finalized/{photo_id}.jpg" in storage.objects


async def test_legacy_cutover_new_user_adoption_queues_rebuild_before_replay(
    pg_harness: PgHarness,
) -> None:
    """An old API instance can write after migration and is rebuilt, not replayed."""
    photo_id = _new_id()
    observation_id = _new_id()
    raw_object = f"pending/{photo_id}.jpg"
    image_buffer = io.BytesIO()
    Image.new("RGB", (80, 60), (20, 90, 40)).save(image_buffer, format="JPEG")
    raw_bytes = image_buffer.getvalue()

    class LegacyStorage:
        def __init__(self) -> None:
            self.objects = {raw_object: raw_bytes}
            self.deleted: list[str] = []

        def get_object_properties(
            self,
            *,
            bucket: str,
            object_name: str,
        ) -> StorageObjectProperties:
            del bucket
            value = self.objects[object_name]
            return StorageObjectProperties(len(value), "image/jpeg", "legacy-etag")

        def fetch_object_bytes(self, *, bucket: str, object_name: str) -> bytes:
            del bucket
            return self.objects[object_name]

        def put_object_bytes(
            self,
            *,
            bucket: str,
            object_name: str,
            data: bytes,
            content_type: str,
            metadata: dict[str, str],
            overwrite: bool,
            expected_sha256: str | None = None,
        ) -> None:
            del bucket, content_type, metadata, expected_sha256
            assert not overwrite
            self.objects[object_name] = data

        def delete_object(self, *, bucket: str, object_name: str) -> None:
            del bucket
            self.deleted.append(object_name)
            self.objects.pop(object_name, None)

    async with pg_harness.sessions() as session:
        identity = await _seed_identity(session)
        # Raw SQL deliberately omits every additive W1 column, matching the
        # old API binary serving during the migration-first compatibility gap.
        await session.execute(
            text(
                "INSERT INTO photos "
                "(id, user_id, bucket, object_name, status, content_type) "
                "VALUES (:id, :user_id, 'observation-verification', :object_name, "
                "'pending', 'image/jpeg')"
            ),
            {"id": photo_id, "user_id": identity.user_id, "object_name": raw_object},
        )
        await session.execute(
            text(
                "INSERT INTO observations "
                "(id, user_id, group_id, photo_id, latitude, longitude, rewards) "
                "VALUES (:id, :user_id, :group_id, :photo_id, 40.75, -73.99, "
                "'[]'::jsonb)"
            ),
            {
                "id": observation_id,
                "user_id": identity.user_id,
                "group_id": identity.group_id,
                "photo_id": photo_id,
            },
        )
        await session.commit()

        legacy_photo = await session.get(models.Photo, photo_id)
        legacy_observation = await session.get(models.Observation, observation_id)
        assert legacy_photo is not None and legacy_photo.submission_key is None
        assert legacy_observation is not None
        assert legacy_observation.submission_key is None
        assert legacy_observation.dispatch_status == "unverified"

    storage = LegacyStorage()
    async with pg_harness.sessions() as session:
        stats = await reconcile_legacy_pending(session, storage)  # type: ignore[arg-type]

    assert stats.canonicalized == 1
    assert stats.rejected == 0
    assert storage.deleted == [raw_object]

    async with pg_harness.sessions() as session:
        observation = await session.get(models.Observation, observation_id)
        photo = await session.get(models.Photo, photo_id)
        outbox = await session.get(models.ModerationOutbox, observation_id)
        rebuilds = (
            (
                await session.execute(
                    select(models.DerivedStateRebuild).where(
                        models.DerivedStateRebuild.user_id == identity.user_id
                    )
                )
            )
            .scalars()
            .all()
        )
        assert observation is not None
        assert photo is not None
        assert outbox is not None
        assert observation.dispatch_status == "unverified"
        assert observation.rewards == []
        assert photo.submission_key == photo.id
        assert observation.submission_key == photo.id
        assert outbox.status == "pending"
        assert len(rebuilds) == 1
        assert rebuilds[0].status == "queued"
        assert rebuilds[0].trigger_observation_id == observation.id

        # Make the row old enough for replay; the active rebuild exclusion,
        # not the grace period, must keep it out of the dispatcher.
        await session.execute(
            update(models.Observation)
            .where(models.Observation.id == observation_id)
            .values(updated_at=datetime.now(UTC) - timedelta(minutes=10))
        )
        await session.commit()

    async with pg_harness.sessions() as session:
        replayed = await replay_dispatcher(session)
    assert replayed == 0

    async with pg_harness.sessions() as session:
        handler_run_count = await session.scalar(
            select(func.count())
            .select_from(models.ObservationHandlerRun)
            .where(models.ObservationHandlerRun.observation_id == observation_id)
        )
        rebuild = await session.get(models.DerivedStateRebuild, rebuilds[0].id)
    assert handler_run_count == 0
    assert rebuild is not None and rebuild.status == "queued"


async def test_replay_reloads_after_rebuild_completes_between_select_and_lock(
    pg_harness: PgHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with pg_harness.sessions() as session:
        rows = await _seed_observation(session)
        observation = await session.get(models.Observation, rows.observation_id)
        assert observation is not None
        observation.dispatch_status = "partial"
        observation.rewards = [
            {
                "type": "first_find",
                "title": "stale",
                "detail": "must be replaced",
                "icon": "stale",
                "weight": 80,
                "payload": {},
            }
        ]
        session.add(
            models.ObservationHandlerRun(
                observation_id=observation.id,
                handler_name="dex",
                handler_version="1",
                status="succeeded",
                state={"legacy": True},
                rewards=list(observation.rewards),
                attempt_count=1,
            )
        )
        await session.commit()
        await session.execute(
            update(models.Observation)
            .where(models.Observation.id == observation.id)
            .values(updated_at=datetime.now(UTC) - timedelta(minutes=10))
        )
        await session.commit()

    from admin import dispatcher_replay as replay_module

    real_acquire_user_lock = acquire_user_lock
    interleaved = False
    completed_job_id: str | None = None

    async def complete_rejection_rebuild_before_lock(
        replay_session: AsyncSession,
        user_id: str,
    ) -> None:
        nonlocal interleaved, completed_job_id
        if not interleaved:
            interleaved = True
            completed_job_id = _new_id()
            async with (
                pg_harness.sessions() as correction_session,
                correction_session.begin(),
            ):
                await real_acquire_user_lock(correction_session, user_id)
                current = (
                    await correction_session.execute(
                        select(models.Observation)
                        .where(models.Observation.id == rows.observation_id)
                        .with_for_update()
                    )
                ).scalar_one()
                current.moderation_status = "rejected"
                current.rejected_at = datetime.now(UTC)
                current.dispatch_status = "unverified"
                current.dispatched_at = None
                current.rewards = []
                correction_session.add(
                    models.DerivedStateRebuild(
                        id=completed_job_id,
                        user_id=user_id,
                        trigger_observation_id=current.id,
                        status="queued",
                        attempt_count=0,
                    )
                )
            async with pg_harness.sessions() as worker_session:
                assert await process_rebuild_job(
                    worker_session,
                    job_id=completed_job_id,
                )
        await real_acquire_user_lock(replay_session, user_id)

    monkeypatch.setattr(
        replay_module,
        "acquire_user_lock",
        complete_rejection_rebuild_before_lock,
    )

    async with pg_harness.sessions() as session:
        replayed = await replay_module.replay(session)

    assert replayed == 0
    assert interleaved
    assert completed_job_id is not None
    async with pg_harness.sessions() as session:
        observation = await session.get(models.Observation, rows.observation_id)
        job = await session.get(models.DerivedStateRebuild, completed_job_id)
        handler_run_count = await session.scalar(
            select(func.count())
            .select_from(models.ObservationHandlerRun)
            .where(models.ObservationHandlerRun.observation_id == rows.observation_id)
        )
    assert observation is not None
    assert observation.moderation_status == "rejected"
    assert observation.rejected_at is not None
    assert observation.dispatch_status == "unverified"
    assert observation.rewards == []
    assert job is not None and job.status == "succeeded"
    assert handler_run_count == 0


async def test_concurrent_idempotency_key_has_exactly_one_winner(
    pg_harness: PgHarness,
) -> None:
    async with pg_harness.sessions() as seed_session:
        identity = await _seed_identity(seed_session)

    key = _new_id()
    start = asyncio.Event()

    async def insert_ledger(resource_id: str) -> str:
        async with pg_harness.sessions() as session:
            session.add(
                models.ObservationIdempotency(
                    user_id=identity.user_id,
                    idempotency_key=key,
                    operation="observation_create",
                    request_hash="b" * 64,
                    resource_id=resource_id,
                )
            )
            await start.wait()
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                return "conflict"
            return "inserted"

    tasks = [
        asyncio.create_task(insert_ledger(_new_id())),
        asyncio.create_task(insert_ledger(_new_id())),
    ]
    start.set()
    outcomes = await asyncio.gather(*tasks)

    async with pg_harness.sessions() as session:
        count = await session.scalar(
            select(func.count())
            .select_from(models.ObservationIdempotency)
            .where(
                models.ObservationIdempotency.user_id == identity.user_id,
                models.ObservationIdempotency.idempotency_key == key,
                models.ObservationIdempotency.operation == "observation_create",
            )
        )

    assert sorted(outcomes) == ["conflict", "inserted"]
    assert count == 1


async def test_concurrent_photo_and_observation_uniqueness(pg_harness: PgHarness) -> None:
    async with pg_harness.sessions() as session:
        identity = await _seed_identity(session)

    shared_photo_key = _new_id()
    photo_start = asyncio.Event()

    async def insert_photo(object_suffix: str) -> str:
        async with pg_harness.sessions() as session:
            session.add(
                models.Photo(
                    id=_new_id(),
                    user_id=identity.user_id,
                    bucket="observation-verification",
                    object_name=f"pending/uploads/{object_suffix}.jpg",
                    status="pending",
                    attachment_status="reserved",
                    submission_key=shared_photo_key,
                )
            )
            await photo_start.wait()
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                return "conflict"
            return "inserted"

    photo_tasks = [
        asyncio.create_task(insert_photo("one")),
        asyncio.create_task(insert_photo("two")),
    ]
    photo_start.set()
    assert sorted(await asyncio.gather(*photo_tasks)) == ["conflict", "inserted"]

    async with pg_harness.sessions() as session:
        first_photo_id, _ = await _add_photo(session, identity)
        second_photo_id, _ = await _add_photo(session, identity)

    shared_observation_key = _new_id()
    observation_start = asyncio.Event()

    async def insert_observation(photo_id: str, submission_key: str) -> str:
        async with pg_harness.sessions() as session:
            session.add(
                _observation(
                    identity,
                    photo_id=photo_id,
                    submission_key=submission_key,
                )
            )
            await observation_start.wait()
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                return "conflict"
            return "inserted"

    same_submission_tasks = [
        asyncio.create_task(insert_observation(first_photo_id, shared_observation_key)),
        asyncio.create_task(insert_observation(second_photo_id, shared_observation_key)),
    ]
    observation_start.set()
    assert sorted(await asyncio.gather(*same_submission_tasks)) == ["conflict", "inserted"]

    async with pg_harness.sessions() as session:
        third_photo_id, _ = await _add_photo(session, identity)

    same_photo_start = asyncio.Event()

    async def insert_for_same_photo(submission_key: str) -> str:
        async with pg_harness.sessions() as session:
            session.add(
                _observation(
                    identity,
                    photo_id=third_photo_id,
                    submission_key=submission_key,
                )
            )
            await same_photo_start.wait()
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                return "conflict"
            return "inserted"

    same_photo_tasks = [
        asyncio.create_task(insert_for_same_photo(_new_id())),
        asyncio.create_task(insert_for_same_photo(_new_id())),
    ]
    same_photo_start.set()
    assert sorted(await asyncio.gather(*same_photo_tasks)) == ["conflict", "inserted"]


async def test_membership_counters_cannot_become_negative(pg_harness: PgHarness) -> None:
    async with pg_harness.sessions() as session:
        identity = await _seed_identity(session)
        with pytest.raises(IntegrityError):
            await session.execute(
                update(models.Membership)
                .where(models.Membership.id == identity.membership_id)
                .values(observation_count=models.Membership.observation_count - 1)
            )
            await session.commit()
        await session.rollback()

    async with pg_harness.sessions() as session:
        membership = await session.get(models.Membership, identity.membership_id)
        assert membership is not None
        assert membership.observation_count == 0
        assert membership.dex_count == 0


class FailingDexHandler:
    name = "dex"
    version = "1"

    def __init__(self, membership_id: str) -> None:
        self.membership_id = membership_id

    async def handle(self, ctx: Context) -> HandlerResult:
        await ctx.db.execute(
            update(models.Membership)
            .where(models.Membership.id == self.membership_id)
            .values(dex_count=models.Membership.dex_count + 100)
        )
        raise RuntimeError("injected dex SQL-path failure")


class SuccessfulDexHandler:
    name = "dex"
    version = "1"

    def __init__(self, membership_id: str) -> None:
        self.membership_id = membership_id

    async def handle(self, ctx: Context) -> HandlerResult:
        await ctx.db.execute(
            update(models.Membership)
            .where(models.Membership.id == self.membership_id)
            .values(dex_count=models.Membership.dex_count + 1)
        )
        return HandlerResult(
            rewards=[
                Reward(
                    type="first_find",
                    title="New species!",
                    detail="Persisted after replay",
                    icon="dex.first_find",
                    weight=80,
                )
            ],
            state={"is_first_find": True},
        )


class IndependentRarityHandler:
    name = "rarity"
    version = "1"
    calls: ClassVar[int] = 0

    async def handle(self, ctx: Context) -> HandlerResult:
        del ctx
        type(self).calls += 1
        return HandlerResult(
            rewards=[
                Reward(
                    type="rarity_tier",
                    title="Rare find",
                    detail="Independent handler survived",
                    icon="rarity.rare",
                    weight=60,
                )
            ],
            state={"tier": "rare"},
        )


class DependentWorldHandler:
    name = "world"
    version = "1"

    def __init__(self) -> None:
        self.calls = 0

    async def handle(self, ctx: Context) -> HandlerResult:
        self.calls += 1
        assert "dex" in ctx.results
        return HandlerResult(
            rewards=[
                Reward(
                    type="world_unlock",
                    title="World unlocked",
                    detail="Dependency recovered",
                    icon="world.unlock",
                    weight=30,
                )
            ]
        )


async def test_dispatcher_savepoint_dependency_persistence_and_replay(
    pg_harness: PgHarness,
) -> None:
    IndependentRarityHandler.calls = 0
    first_world = DependentWorldHandler()
    async with pg_harness.sessions() as session:
        rows = await _seed_observation(session)
        ctx = await _context(session, rows)
        first_rewards = await dispatch(
            ctx,
            [
                FailingDexHandler(rows.identity.membership_id),
                IndependentRarityHandler(),
                first_world,
            ],
        )

    async with pg_harness.sessions() as session:
        runs = {
            run.handler_name: run
            for run in (
                await session.execute(
                    select(models.ObservationHandlerRun).where(
                        models.ObservationHandlerRun.observation_id == rows.observation_id
                    )
                )
            ).scalars()
        }
        observation = await session.get(models.Observation, rows.observation_id)
        membership = await session.get(models.Membership, rows.identity.membership_id)

    assert [reward.type for reward in first_rewards] == ["rarity_tier"]
    assert runs["dex"].status == "failed"
    assert runs["dex"].attempt_count == 1
    assert runs["rarity"].status == "succeeded"
    assert runs["rarity"].attempt_count == 1
    assert runs["world"].status == "blocked"
    assert runs["world"].attempt_count == 0
    assert first_world.calls == 0
    assert observation is not None and observation.dispatch_status == "partial"
    assert observation.rewards[0]["type"] == "rarity_tier"
    assert membership is not None and membership.dex_count == 0

    replay_world = DependentWorldHandler()
    async with pg_harness.sessions() as session:
        replay_ctx = await _context(session, rows)
        replay_rewards = await dispatch(
            replay_ctx,
            [
                SuccessfulDexHandler(rows.identity.membership_id),
                IndependentRarityHandler(),
                replay_world,
            ],
        )

    async with pg_harness.sessions() as session:
        replay_runs = {
            run.handler_name: run
            for run in (
                await session.execute(
                    select(models.ObservationHandlerRun).where(
                        models.ObservationHandlerRun.observation_id == rows.observation_id
                    )
                )
            ).scalars()
        }
        observation = await session.get(models.Observation, rows.observation_id)
        membership = await session.get(models.Membership, rows.identity.membership_id)

    assert [reward.type for reward in replay_rewards] == [
        "first_find",
        "rarity_tier",
        "world_unlock",
    ]
    assert replay_runs["dex"].status == "succeeded"
    assert replay_runs["dex"].attempt_count == 2
    assert replay_runs["rarity"].attempt_count == 1
    assert replay_runs["world"].status == "succeeded"
    assert replay_runs["world"].attempt_count == 1
    assert IndependentRarityHandler.calls == 1
    assert replay_world.calls == 1
    assert observation is not None and observation.dispatch_status == "complete"
    assert observation.dispatched_at is not None
    assert [reward["type"] for reward in observation.rewards] == [
        "first_find",
        "rarity_tier",
        "world_unlock",
    ]
    assert membership is not None and membership.dex_count == 1


async def test_handler_version_mismatch_blocks_stale_predecessor_and_dependent(
    pg_harness: PgHarness,
) -> None:
    class VersionTwoDex(SuccessfulDexHandler):
        version = "2"

    async with pg_harness.sessions() as session:
        rows = await _seed_observation(session)
        session.add_all(
            [
                models.ObservationHandlerRun(
                    observation_id=rows.observation_id,
                    handler_name="dex",
                    handler_version="1",
                    status="succeeded",
                    state={"is_first_find": True},
                    rewards=[],
                    attempt_count=1,
                ),
                models.ObservationHandlerRun(
                    observation_id=rows.observation_id,
                    handler_name="world",
                    handler_version="1",
                    status="pending",
                    state={},
                    rewards=[],
                    attempt_count=0,
                ),
            ]
        )
        await session.commit()
        world = DependentWorldHandler()
        rewards = await dispatch(
            await _context(session, rows),
            [VersionTwoDex(rows.identity.membership_id), world],
        )

    async with pg_harness.sessions() as session:
        runs = {
            run.handler_name: run
            for run in (
                await session.execute(
                    select(models.ObservationHandlerRun).where(
                        models.ObservationHandlerRun.observation_id == rows.observation_id
                    )
                )
            ).scalars()
        }

    assert rewards == []
    assert runs["dex"].status == "blocked"
    assert "handler version changed" in (runs["dex"].last_error or "")
    assert runs["world"].status == "blocked"
    assert world.calls == 0


def _expedition_body(expedition_id: str) -> models.JsonDict:
    return {
        "id": expedition_id,
        "title": "Replay Gate",
        "tier": 1,
        "duration_minutes": 10,
        "environments": ["other"],
        "intro": "Find two organisms.",
        "outro": "Complete.",
        "prerequisites": [],
        "steps": [
            {
                "id": "first",
                "description": "First organism",
                "match": {"kind": "any_organism"},
            },
            {
                "id": "second",
                "description": "Second organism",
                "match": {"kind": "any_organism"},
            },
        ],
    }


async def test_expedition_contribution_gate_blocks_replay_from_advancing_again(
    pg_harness: PgHarness,
) -> None:
    async with pg_harness.sessions() as session:
        rows = await _seed_observation(session)
        expedition_id = "postgres_replay_gate"
        started_at = datetime.now(UTC) - timedelta(days=1)
        session.add_all(
            [
                models.ExpeditionContent(
                    id=expedition_id,
                    tier=1,
                    content_hash="c" * 64,
                    body=_expedition_body(expedition_id),
                    archived=False,
                ),
                models.ExpeditionProgress(
                    id=_new_id(),
                    user_id=rows.identity.user_id,
                    group_id=rows.identity.group_id,
                    expedition_id=expedition_id,
                    completed_steps={},
                    completed_at=None,
                    created_at=started_at,
                ),
            ]
        )
        await session.commit()

        ctx = await _context(session, rows)
        first = await ExpeditionHandler().handle(ctx)
        await session.commit()

    async with pg_harness.sessions() as session:
        replay_ctx = await _context(session, rows)
        replay = await ExpeditionHandler().handle(replay_ctx)
        await session.commit()

    async with pg_harness.sessions() as session:
        progress = (
            await session.execute(
                select(models.ExpeditionProgress).where(
                    models.ExpeditionProgress.user_id == rows.identity.user_id,
                    models.ExpeditionProgress.expedition_id == expedition_id,
                )
            )
        ).scalar_one()
        contributions = (
            (
                await session.execute(
                    select(models.ExpeditionObservationContribution).where(
                        models.ExpeditionObservationContribution.observation_id
                        == rows.observation_id
                    )
                )
            )
            .scalars()
            .all()
        )

    assert [reward.type for reward in first.rewards] == ["expedition_step"]
    assert first.rewards[0].payload["step_id"] == "first"
    assert replay.rewards == []
    assert set(progress.completed_steps) == {"first"}
    assert progress.completed_at is None
    assert len(contributions) == 1
    assert contributions[0].step_id == "first"


async def test_approve_lock_wins_over_reject_and_stale_sweep(pg_harness: PgHarness) -> None:
    async with pg_harness.sessions() as session:
        rows = await _seed_quarantine_review(session)

    lock_acquired = asyncio.Event()
    release_winner = asyncio.Event()

    async def approve_winner() -> str:
        async with pg_harness.sessions() as session:
            adult = await session.get(models.User, rows.observation.identity.parent_id)
            assert adult is not None
            review = await _load_review_for_resolution(session, adult, rows.review_id)
            photo = await session.get(models.Photo, rows.observation.photo_id)
            observation = await session.get(models.Observation, rows.observation.observation_id)
            assert photo is not None
            assert observation is not None
            lock_acquired.set()
            await release_winner.wait()
            review.status = "approved"
            review.reviewer_user_id = adult.id
            review.resolved_at = datetime.now(UTC)
            photo.status = "clean"
            observation.moderation_status = "clean"
            observation.moderation_source = "adult"
            await session.commit()
            return "approved"

    async def reject_loser() -> int:
        await lock_acquired.wait()
        async with pg_harness.sessions() as session:
            adult = await session.get(models.User, rows.observation.identity.parent_id)
            assert adult is not None
            try:
                review = await _load_review_for_resolution(
                    session, adult, rows.review_id, lock=False
                )
                await reject_review_item(session, review=review, reviewer_user_id=adult.id)
            except HTTPException as exc:
                await session.rollback()
                return exc.status_code
            except ReviewResolutionConflict:
                await session.rollback()
                return 409
            await session.commit()
            return 200

    async def stale_loser() -> int:
        await lock_acquired.wait()
        async with pg_harness.sessions() as session:
            return await sweep(session, storage=MagicMock())

    winner_task = asyncio.create_task(approve_winner())
    await lock_acquired.wait()
    reject_task = asyncio.create_task(reject_loser())
    stale_task = asyncio.create_task(stale_loser())
    try:
        await asyncio.sleep(0.05)
        stale_count = await stale_task
    finally:
        release_winner.set()

    winner, reject_status = await asyncio.gather(winner_task, reject_task)
    async with pg_harness.sessions() as session:
        review = await session.get(models.ReviewQueueItem, rows.review_id)
        photo = await session.get(models.Photo, rows.observation.photo_id)
        observation = await session.get(models.Observation, rows.observation.observation_id)
        rebuild_count = await session.scalar(
            select(func.count()).select_from(models.DerivedStateRebuild)
        )

    assert winner == "approved"
    assert reject_status == 409
    assert stale_count == 0
    assert review is not None and review.status == "approved"
    assert photo is not None and photo.status == "clean"
    assert observation is not None and observation.moderation_status == "clean"
    assert rebuild_count == 0


async def test_reject_lock_wins_over_approve_and_stale_sweep(pg_harness: PgHarness) -> None:
    async with pg_harness.sessions() as session:
        rows = await _seed_quarantine_review(session)

    lock_acquired = asyncio.Event()
    release_winner = asyncio.Event()

    async def reject_winner() -> str:
        async with pg_harness.sessions() as session:
            adult = await session.get(models.User, rows.observation.identity.parent_id)
            assert adult is not None
            review = await _load_review_for_resolution(session, adult, rows.review_id, lock=False)
            await reject_review_item(session, review=review, reviewer_user_id=adult.id)
            lock_acquired.set()
            await release_winner.wait()
            await session.commit()
            return "rejected"

    async def approve_loser() -> int:
        await lock_acquired.wait()
        async with pg_harness.sessions() as session:
            adult = await session.get(models.User, rows.observation.identity.parent_id)
            assert adult is not None
            try:
                await _load_review_for_resolution(session, adult, rows.review_id)
            except HTTPException as exc:
                await session.rollback()
                return exc.status_code
            await session.rollback()
            return 200

    async def stale_loser() -> int:
        await lock_acquired.wait()
        async with pg_harness.sessions() as session:
            return await sweep(session, storage=MagicMock())

    winner_task = asyncio.create_task(reject_winner())
    await lock_acquired.wait()
    approve_task = asyncio.create_task(approve_loser())
    stale_task = asyncio.create_task(stale_loser())
    try:
        await asyncio.sleep(0.05)
        stale_count = await stale_task
    finally:
        release_winner.set()

    winner, approve_status = await asyncio.gather(winner_task, approve_task)
    async with pg_harness.sessions() as session:
        review = await session.get(models.ReviewQueueItem, rows.review_id)
        photo = await session.get(models.Photo, rows.observation.photo_id)
        observation = await session.get(models.Observation, rows.observation.observation_id)
        rebuilds = (await session.execute(select(models.DerivedStateRebuild))).scalars().all()

    assert winner == "rejected"
    assert approve_status == 409
    assert stale_count == 0
    assert review is not None and review.status == "rejected"
    assert photo is not None and photo.status == "deleted"
    assert observation is not None and observation.moderation_status == "rejected"
    assert observation.rejected_at is not None
    assert len(rebuilds) == 1
    assert rebuilds[0].user_id == rows.observation.identity.user_id


class LockProbeHandler:
    name = "dex"
    version = "1"

    def __init__(self, started: asyncio.Event) -> None:
        self.started = started

    async def handle(self, ctx: Context) -> HandlerResult:
        del ctx
        self.started.set()
        return HandlerResult(rewards=[])


async def test_dispatch_waits_for_same_user_rebuild_lock(pg_harness: PgHarness) -> None:
    async with pg_harness.sessions() as session:
        rows = await _seed_observation(session)

    rebuild_lock_acquired = asyncio.Event()
    release_rebuild_lock = asyncio.Event()
    dispatch_attempted = asyncio.Event()
    handler_started = asyncio.Event()

    async def hold_rebuild_lock() -> None:
        async with pg_harness.sessions() as session, session.begin():
            await acquire_user_lock(session, rows.identity.user_id)
            rebuild_lock_acquired.set()
            await release_rebuild_lock.wait()

    async def run_dispatch() -> None:
        async with pg_harness.sessions() as session:
            ctx = await _context(session, rows)
            dispatch_attempted.set()
            await dispatch(ctx, [LockProbeHandler(handler_started)])

    rebuild_task = asyncio.create_task(hold_rebuild_lock())
    await rebuild_lock_acquired.wait()
    dispatch_task = asyncio.create_task(run_dispatch())
    await dispatch_attempted.wait()
    try:
        await asyncio.sleep(0.15)
        handler_started_while_rebuild_locked = handler_started.is_set()
    finally:
        release_rebuild_lock.set()
        await asyncio.gather(rebuild_task, dispatch_task)

    assert not handler_started_while_rebuild_locked
    assert handler_started.is_set()


async def test_rebuild_claim_uses_user_lock_before_job_row_lock(
    pg_harness: PgHarness,
) -> None:
    async with pg_harness.sessions() as session:
        identity = await _seed_identity(session)
        observation = await _add_observation_for_identity(session, identity)
        job_id = _new_id()
        session.add(
            models.DerivedStateRebuild(
                id=job_id,
                user_id=identity.user_id,
                trigger_observation_id=None,
                status="queued",
                attempt_count=0,
            )
        )
        await session.commit()

    correction_holds_user_lock = asyncio.Event()
    worker_started = asyncio.Event()
    correction_updated_job = asyncio.Event()

    async def correction() -> None:
        async with pg_harness.sessions() as session, session.begin():
            await acquire_user_lock(session, identity.user_id)
            correction_holds_user_lock.set()
            await worker_started.wait()
            # Give the worker enough time to reach its first lock. With the
            # old job-row -> user-lock order this creates the inverse half of
            # the deadlock when enqueue_rebuild flushes the trigger update.
            await asyncio.sleep(0.15)
            job = await enqueue_rebuild(
                session,
                user_id=identity.user_id,
                trigger_observation_id=observation.observation_id,
            )
            assert job.id == job_id
            await session.flush()
            correction_updated_job.set()

    async def worker() -> bool:
        await correction_holds_user_lock.wait()
        worker_started.set()
        async with pg_harness.sessions() as session:
            return await process_rebuild_job(session, job_id=job_id)

    correction_task = asyncio.create_task(correction())
    await correction_holds_user_lock.wait()
    worker_task = asyncio.create_task(worker())
    await asyncio.wait_for(correction_updated_job.wait(), timeout=3.0)
    await correction_task
    assert await asyncio.wait_for(worker_task, timeout=10.0)

    async with pg_harness.sessions() as session:
        job = await session.get(models.DerivedStateRebuild, job_id)
    assert job is not None
    assert job.status == "succeeded"
    assert job.trigger_observation_id == observation.observation_id


async def test_moderation_retry_repairs_moved_terminal_state_atomically(
    pg_harness: PgHarness,
) -> None:
    async with pg_harness.sessions() as session:
        rows = await _seed_observation(session)
        photo = await session.get(models.Photo, rows.photo_id)
        observation = await session.get(models.Observation, rows.observation_id)
        assert photo is not None
        assert observation is not None
        original_object = photo.object_name
        photo.status = "clean"
        photo.object_name = f"observations/{photo.id}.jpg"
        photo.canonical_object_name = photo.object_name
        observation.moderation_status = "clean"
        observation.moderation_source = "azure"
        session.add(
            models.ModerationOutbox(
                observation_id=observation.id,
                photo_id=photo.id,
                status="processing",
                lease_until=datetime.now(UTC) - timedelta(minutes=1),
                last_error="consumer crashed after destination commit",
            )
        )
        await session.commit()

    body = json.dumps(
        {
            "observation_id": rows.observation_id,
            "photo_id": rows.photo_id,
            "bucket": "observation-verification",
            "object_name": original_object,
        }
    )
    async with pg_harness.sessions() as session:
        disposition = await process_moderation_message(
            session,
            object(),  # terminal repair must not touch storage
            object(),  # terminal repair must not call the provider
            Settings(env="local", service_bus_namespace=""),
            body=body,
        )

    async with pg_harness.sessions() as session:
        outbox = await session.get(models.ModerationOutbox, rows.observation_id)
        observation = await session.get(models.Observation, rows.observation_id)
        photo = await session.get(models.Photo, rows.photo_id)

    assert disposition == "complete"
    assert outbox is not None and outbox.status == "succeeded"
    assert outbox.lease_until is None
    assert outbox.last_error is None
    assert observation is not None and observation.moderation_status == "clean"
    assert photo is not None and photo.object_name == f"observations/{rows.photo_id}.jpg"


async def test_rebuild_promotes_surviving_observation_to_dex_first_find(
    pg_harness: PgHarness,
) -> None:
    first_time = datetime.now(UTC) - timedelta(days=2)
    second_time = first_time + timedelta(hours=1)
    async with pg_harness.sessions() as session:
        identity = await _seed_identity(session)
        first = await _add_observation_for_identity(
            session,
            identity,
            taxon_id=3,
            observed_at=first_time,
        )
        second = await _add_observation_for_identity(
            session,
            identity,
            taxon_id=3,
            observed_at=second_time,
        )

        async with session.begin():
            await rebuild_user_state(session, user_id=identity.user_id)

    async with pg_harness.sessions() as session:
        initial_dex = (
            await session.execute(
                select(models.DexEntry).where(models.DexEntry.user_id == identity.user_id)
            )
        ).scalar_one()
        assert initial_dex.first_observation_id == first.observation_id
        assert initial_dex.observation_count == 2
        assert initial_dex.latest_seen_at == second_time

        first_observation = await session.get(models.Observation, first.observation_id)
        assert first_observation is not None
        first_observation.moderation_status = "rejected"
        first_observation.rejected_at = datetime.now(UTC)
        await session.commit()

        async with session.begin():
            await rebuild_user_state(session, user_id=identity.user_id)

    async with pg_harness.sessions() as session:
        membership = await session.get(models.Membership, identity.membership_id)
        dex = (
            await session.execute(
                select(models.DexEntry).where(models.DexEntry.user_id == identity.user_id)
            )
        ).scalar_one()
        rejected = await session.get(models.Observation, first.observation_id)
        surviving = await session.get(models.Observation, second.observation_id)
        rejected_run_count = await session.scalar(
            select(func.count())
            .select_from(models.ObservationHandlerRun)
            .where(models.ObservationHandlerRun.observation_id == first.observation_id)
        )

    assert membership is not None
    assert membership.observation_count == 1
    assert membership.dex_count == 1
    assert membership.last_observed_at == second_time
    assert dex.first_observation_id == second.observation_id
    assert dex.first_seen_at == second_time
    assert dex.observation_count == 1
    assert dex.latest_seen_at == second_time
    assert rejected is not None
    assert rejected.rewards == []
    assert rejected.dispatch_status == "unverified"
    assert surviving is not None
    assert surviving.dispatch_status == "complete"
    assert any(reward["type"] == "first_find" for reward in surviving.rewards)
    assert rejected_run_count == 0


async def test_rejected_repeat_does_not_change_dex_count_or_latest_seen(
    pg_harness: PgHarness,
) -> None:
    accepted_time = datetime.now(UTC) - timedelta(days=2)
    rejected_time = accepted_time + timedelta(days=1)
    async with pg_harness.sessions() as session:
        identity = await _seed_identity(session)
        accepted = await _add_observation_for_identity(
            session,
            identity,
            taxon_id=3,
            observed_at=accepted_time,
        )
        rejected = await _add_observation_for_identity(
            session,
            identity,
            taxon_id=3,
            observed_at=rejected_time,
        )
        rejected_observation = await session.get(models.Observation, rejected.observation_id)
        assert rejected_observation is not None
        rejected_observation.moderation_status = "rejected"
        rejected_observation.rejected_at = datetime.now(UTC)
        await session.commit()

        async with session.begin():
            await rebuild_user_state(session, user_id=identity.user_id)

    async with pg_harness.sessions() as session:
        dex = (
            await session.execute(
                select(models.DexEntry).where(models.DexEntry.user_id == identity.user_id)
            )
        ).scalar_one()

    assert dex.first_observation_id == accepted.observation_id
    assert dex.first_seen_at == accepted_time
    assert dex.observation_count == 1
    assert dex.latest_seen_at == accepted_time


async def test_backdated_repeat_updates_first_seen_without_second_celebration(
    pg_harness: PgHarness,
) -> None:
    newer_time = datetime.now(UTC) - timedelta(days=1)
    older_time = newer_time - timedelta(days=2)
    async with pg_harness.sessions() as session:
        identity = await _seed_identity(session)
        newer = await _add_observation_for_identity(
            session,
            identity,
            taxon_id=3,
            observed_at=newer_time,
        )
        first_rewards = await dispatch(await _context(session, newer), [DexHandler()])

        older = await _add_observation_for_identity(
            session,
            identity,
            taxon_id=3,
            observed_at=older_time,
        )
        repeat_rewards = await dispatch(await _context(session, older), [DexHandler()])

    async with pg_harness.sessions() as session:
        membership = await session.get(models.Membership, identity.membership_id)
        dex = (
            await session.execute(
                select(models.DexEntry).where(models.DexEntry.user_id == identity.user_id)
            )
        ).scalar_one()

    assert [reward.type for reward in first_rewards] == ["first_find"]
    assert [reward.type for reward in repeat_rewards] == ["repeat_find"]
    assert membership is not None and membership.dex_count == 1
    assert dex.first_observation_id == older.observation_id
    assert dex.first_seen_at == older_time
    assert dex.observation_count == 2
    assert dex.latest_seen_at == newer_time


async def test_rejection_revokes_bytes_before_tombstone_and_queues_rebuild(
    pg_harness: PgHarness,
) -> None:
    async with pg_harness.sessions() as session:
        rows = await _seed_quarantine_review(session)
        review = await session.get(models.ReviewQueueItem, rows.review_id)
        photo = await session.get(models.Photo, rows.observation.photo_id)
        assert review is not None
        assert photo is not None
        source_object = photo.object_name
        held_object = f"rejected/held/{photo.id}.jpg"
        storage = RevocationStorage({source_object: _CANONICAL_BYTES})
        rebuild = await revoke_and_reject_review_item(
            session,
            storage=storage,  # type: ignore[arg-type]
            review=review,
            reviewer_user_id=rows.observation.identity.parent_id,
            source="adult_review",
        )

    async with pg_harness.sessions() as session:
        review = await session.get(models.ReviewQueueItem, rows.review_id)
        photo = await session.get(models.Photo, rows.observation.photo_id)
        observation = await session.get(models.Observation, rows.observation.observation_id)
        revocation = await session.get(models.PhotoRevocation, rows.observation.photo_id)

    assert source_object not in storage.objects
    assert storage.objects[held_object] == _CANONICAL_BYTES
    assert review is not None and review.status == "rejected"
    assert photo is not None and photo.status == "deleted"
    assert photo.object_name == held_object
    assert observation is not None and observation.moderation_status == "rejected"
    assert revocation is not None and revocation.state == "succeeded"
    assert rebuild is not None and rebuild.user_id == rows.observation.identity.user_id


async def test_approved_clean_photo_can_be_revoked_and_deny_gate_outlives_review(
    pg_harness: PgHarness,
) -> None:
    approved_at = datetime.now(UTC) - timedelta(hours=1)
    async with pg_harness.sessions() as session:
        rows = await _seed_quarantine_review(session)
        review = await session.get(models.ReviewQueueItem, rows.review_id)
        photo = await session.get(models.Photo, rows.observation.photo_id)
        observation = await session.get(models.Observation, rows.observation.observation_id)
        assert review is not None
        assert photo is not None
        assert observation is not None
        review.status = "approved"
        review.reviewer_user_id = rows.observation.identity.parent_id
        review.resolved_at = approved_at
        photo.status = "clean"
        photo.attachment_status = "attached"
        photo.object_name = f"observations/{photo.id}.jpg"
        photo.canonical_object_name = photo.object_name
        observation.moderation_status = "clean"
        observation.moderation_source = "adult"
        observation.moderation_policy_version = "adult-review-v1"
        await session.commit()

        clean_source = photo.object_name
        held_object = f"rejected/held/{photo.id}.jpg"
        storage = RevocationStorage({clean_source: _CANONICAL_BYTES})
        rebuild = await revoke_and_reject_review_item(
            session,
            storage=storage,  # type: ignore[arg-type]
            review=review,
            reviewer_user_id=rows.observation.identity.parent_id,
            source="adult_revocation",
            claim_review_status="approved",
        )

    assert clean_source not in storage.objects
    assert storage.objects[held_object] == _CANONICAL_BYTES
    assert rebuild is not None

    async with pg_harness.sessions() as session:
        review = await session.get(models.ReviewQueueItem, rows.review_id)
        photo = await session.get(models.Photo, rows.observation.photo_id)
        observation = await session.get(models.Observation, rows.observation.observation_id)
        revocation = await session.get(models.PhotoRevocation, rows.observation.photo_id)
        assert review is not None and review.status == "revoked"
        assert review.reviewer_user_id == rows.observation.identity.parent_id
        assert review.resolved_at == approved_at
        assert photo is not None and photo.status == "deleted"
        assert photo.object_name == held_object
        assert observation is not None
        assert observation.moderation_status == "rejected"
        assert observation.moderation_policy_version == "adult-revocation-v1"
        assert revocation is not None
        assert revocation.state == "succeeded"
        assert revocation.claim_review_status == "approved"
        assert revocation.requesting_actor_user_id == rows.observation.identity.parent_id

        await session.delete(review)
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()
        assert await session.get(models.PhotoRevocation, rows.observation.photo_id) is not None


async def test_rejection_recovers_after_db_failure_once_clean_source_is_gone(
    pg_harness: PgHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with pg_harness.sessions() as session:
        rows = await _seed_quarantine_review(session)
        review = await session.get(models.ReviewQueueItem, rows.review_id)
        photo = await session.get(models.Photo, rows.observation.photo_id)
        assert review is not None
        assert photo is not None
        source_object = photo.object_name
        held_object = f"rejected/held/{photo.id}.jpg"
        storage = RevocationStorage({source_object: _CANONICAL_BYTES})

        original_commit = session.commit
        commit_count = 0

        async def fail_final_commit() -> None:
            nonlocal commit_count
            commit_count += 1
            if commit_count == 2:
                raise RuntimeError("injected final commit failure")
            await original_commit()

        monkeypatch.setattr(session, "commit", fail_final_commit)
        with pytest.raises(PhotoRevocationPending):
            await revoke_and_reject_review_item(
                session,
                storage=storage,  # type: ignore[arg-type]
                review=review,
                reviewer_user_id=rows.observation.identity.parent_id,
                source="adult_review",
            )

    assert source_object not in storage.objects
    assert storage.objects[held_object] == _CANONICAL_BYTES
    async with pg_harness.sessions() as session:
        review = await session.get(models.ReviewQueueItem, rows.review_id)
        photo = await session.get(models.Photo, rows.observation.photo_id)
        revocation = await session.get(models.PhotoRevocation, rows.observation.photo_id)
        assert review is not None and review.status == "pending"
        assert photo is not None and photo.status == "quarantine"
        assert revocation is not None and revocation.state == "copying"

        await revoke_and_reject_review_item(
            session,
            storage=storage,  # type: ignore[arg-type]
            review=review,
            reviewer_user_id=rows.observation.identity.parent_id,
            source="adult_review",
        )

    async with pg_harness.sessions() as session:
        review = await session.get(models.ReviewQueueItem, rows.review_id)
        photo = await session.get(models.Photo, rows.observation.photo_id)
        revocation = await session.get(models.PhotoRevocation, rows.observation.photo_id)
    assert review is not None and review.status == "rejected"
    assert photo is not None and photo.status == "deleted"
    assert revocation is not None and revocation.state == "succeeded"
    assert revocation.attempt_count == 2


class ProbeHandler:
    version = "1"

    def __init__(self, name: str) -> None:
        self.name = name

    async def handle(self, ctx: Context) -> HandlerResult:
        del ctx
        return HandlerResult(rewards=[])


@pytest.mark.slow
async def test_dispatcher_lightweight_p95_under_300ms(pg_harness: PgHarness) -> None:
    sample_count = int(os.getenv("OBSERVATION_DISPATCHER_PROBE_RUNS", "50"))
    if sample_count < 20:
        pytest.fail("OBSERVATION_DISPATCHER_PROBE_RUNS must be at least 20")

    async with pg_harness.sessions() as session:
        identity = await _seed_identity(session)
        rows: list[ObservationRows] = []
        for index in range(sample_count):
            photo_id, submission_key = await _add_photo(session, identity)
            observation_id = _new_id()
            session.add(
                _observation(
                    identity,
                    photo_id=photo_id,
                    submission_key=submission_key,
                    observation_id=observation_id,
                    taxon_id=10_000 + index,
                )
            )
            await session.commit()
            rows.append(
                ObservationRows(
                    identity=identity,
                    photo_id=photo_id,
                    observation_id=observation_id,
                    submission_key=submission_key,
                )
            )

    handlers = [
        ProbeHandler("dex"),
        ProbeHandler("rarity"),
        ProbeHandler("world"),
        ProbeHandler("expedition"),
    ]
    durations_ms: list[float] = []
    for observation_rows in rows:
        async with pg_harness.sessions() as session:
            ctx = await _context(session, observation_rows)
            started = perf_counter()
            await dispatch(ctx, handlers)
            durations_ms.append((perf_counter() - started) * 1000)

    ordered = sorted(durations_ms)
    p95_index = max(0, math.ceil(0.95 * len(ordered)) - 1)
    p95_ms = ordered[p95_index]
    print(
        "dispatcher_probe "
        f"samples={len(ordered)} p95_ms={p95_ms:.2f} "
        f"min_ms={ordered[0]:.2f} max_ms={ordered[-1]:.2f}"
    )
    assert p95_ms < 300.0
