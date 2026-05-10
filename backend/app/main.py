"""Dragonfly API entry point.

FastAPI app for Cloud Run, with the Mangum handler retained for the legacy AWS
path until that compatibility layer is intentionally removed.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from mangum import Mangum

from app.api.routes.auth import router as auth_router
from app.api.routes.geocode import router as geocode_router
from app.api.routes.groups import router as groups_router
from app.api.routes.meta import platform_router, v1_router
from app.api.routes.observations import router as observations_router
from app.api.routes.photos import router as photos_router
from app.core.config import Settings, get_settings
from app.core.errors import install_exception_handlers
from app.core.logging import configure_logging, install_request_logging
from app.db.session import Database


def create_app(settings: Settings | None = None) -> FastAPI:
    active_settings = settings or get_settings()
    database = Database(active_settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configure_logging(active_settings)
        log = structlog.get_logger()
        log.info(
            "api.startup",
            env=active_settings.env,
            gcp_project_id=active_settings.gcp_project_id,
        )
        yield
        await database.close()
        log.info("api.shutdown")

    app = FastAPI(
        title=active_settings.app_name,
        version=active_settings.app_version,
        lifespan=lifespan,
    )
    app.state.settings = active_settings
    app.state.database = database

    app.add_middleware(
        CORSMiddleware,
        allow_origins=active_settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["*"],
    )

    install_exception_handlers(app)
    install_request_logging(app)
    app.include_router(platform_router)
    app.include_router(v1_router)
    app.include_router(auth_router)
    app.include_router(groups_router)
    app.include_router(photos_router)
    app.include_router(observations_router)
    app.include_router(geocode_router)
    return app


app = create_app()

# Lambda entry point. API Gateway invokes this on the legacy AWS path.
handler = Mangum(app, lifespan="on")
