"""Metadata and platform probe routes."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel

from app.core.config import Environment, Settings, get_request_settings
from app.db.session import DatabaseProbe, get_request_database

platform_router = APIRouter(tags=["meta"])
v1_router = APIRouter(prefix="/v1", tags=["meta"])
SettingsDep = Annotated[Settings, Depends(get_request_settings)]
DatabaseDep = Annotated[DatabaseProbe, Depends(get_request_database)]


class HealthResponse(BaseModel):
    status: Literal["ok"]
    env: Environment
    version: str


class ReadinessCheck(BaseModel):
    name: str
    status: Literal["ok", "skipped", "not_ready"]
    detail: str | None = None


class ReadinessResponse(BaseModel):
    status: Literal["ready", "not_ready"]
    env: Environment
    version: str
    checks: list[ReadinessCheck]


class ApiMetaResponse(BaseModel):
    name: str
    env: Environment
    version: str


def _health_response(settings: Settings) -> HealthResponse:
    return HealthResponse(
        status="ok",
        env=settings.env,
        version=settings.app_version,
    )


@platform_router.get("/health", response_model=HealthResponse)
def health(settings: SettingsDep) -> HealthResponse:
    """Liveness probe. Does not touch external services."""
    return _health_response(settings)


@platform_router.get("/ready", response_model=ReadinessResponse)
async def ready(
    response: Response,
    settings: SettingsDep,
    database: DatabaseDep,
) -> ReadinessResponse:
    """Readiness probe for runtime configuration and optional dependencies."""
    checks = [
        ReadinessCheck(name="settings", status="ok"),
    ]

    if settings.readiness_database_required:
        database_status, database_detail = await database.readiness()
        checks.append(
            ReadinessCheck(
                name="database",
                status=database_status,
                detail=database_detail,
            )
        )
    else:
        checks.append(
            ReadinessCheck(
                name="database",
                status="skipped",
                detail="set HINTERLAND_READINESS_DATABASE_REQUIRED=true to enforce it",
            )
        )

    overall_status: Literal["ready", "not_ready"] = (
        "not_ready" if any(check.status == "not_ready" for check in checks) else "ready"
    )
    if overall_status == "not_ready":
        response.status_code = 503

    return ReadinessResponse(
        status=overall_status,
        env=settings.env,
        version=settings.app_version,
        checks=checks,
    )


@v1_router.get("/meta", response_model=ApiMetaResponse)
def meta(settings: SettingsDep) -> ApiMetaResponse:
    """Versioned API metadata."""
    return ApiMetaResponse(
        name=settings.app_name,
        env=settings.env,
        version=settings.app_version,
    )
