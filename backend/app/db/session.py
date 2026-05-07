"""Async Postgres session and readiness helpers."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated, Literal, Protocol, Self

from fastapi import Depends, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import Settings

DatabaseStatus = Literal["ok", "not_ready"]


class DatabaseProbe(Protocol):
    async def readiness(self) -> tuple[DatabaseStatus, str | None]:
        """Return readiness status for the database dependency."""


class Database:
    """Owns SQLAlchemy async engine lifecycle for the FastAPI app."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._engine: AsyncEngine | None = None
        self._sessions: async_sessionmaker[AsyncSession] | None = None

    @property
    def engine(self) -> AsyncEngine:
        if self._engine is None:
            self._engine = create_async_engine(
                self._settings.sqlalchemy_database_url,
                echo=self._settings.database_echo_sql,
                pool_pre_ping=True,
                pool_size=self._settings.database_pool_size,
                max_overflow=self._settings.database_max_overflow,
            )
        return self._engine

    @property
    def sessions(self) -> async_sessionmaker[AsyncSession]:
        if self._sessions is None:
            self._sessions = async_sessionmaker(
                self.engine,
                expire_on_commit=False,
                autoflush=False,
            )
        return self._sessions

    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self.sessions() as session:
            yield session

    async def readiness(self) -> tuple[DatabaseStatus, str | None]:
        if not self._settings.database_configured:
            return "not_ready", "database settings are missing"

        try:
            async with self.engine.connect() as connection:
                await connection.execute(text("select 1"))
        except Exception as exc:
            return "not_ready", str(exc)

        return "ok", "database connection succeeded"

    async def close(self) -> None:
        if self._engine is not None:
            await self._engine.dispose()
            self._engine = None
            self._sessions = None

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_exc_info: object) -> None:
        await self.close()


def get_request_database(request: Request) -> DatabaseProbe:
    return request.app.state.database  # type: ignore[no-any-return]


async def get_db_session(request: Request) -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding an AsyncSession bound to the request's DB.

    Each request gets a fresh session that's closed when the request returns.
    Override in tests via `app.dependency_overrides[get_db_session]`.
    """
    database: Database = request.app.state.database
    async with database.sessions() as session:
        yield session


DbSessionDep = Annotated[AsyncSession, Depends(get_db_session)]
