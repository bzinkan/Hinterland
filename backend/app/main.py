"""Hinterland API entry point for Azure Container Apps."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes.auth import router as auth_router
from app.api.routes.auth import well_known_router
from app.api.routes.dex import router as dex_router
from app.api.routes.expeditions import router as expeditions_router
from app.api.routes.geocode import router as geocode_router
from app.api.routes.groups import router as groups_router
from app.api.routes.meta import platform_router, v1_router
from app.api.routes.observations import router as observations_router
from app.api.routes.photos import router as photos_router
from app.api.routes.review_queue import router as review_queue_router
from app.api.routes.sanctuary import router as sanctuary_router
from app.api.routes.species import router as species_router
from app.api.routes.taxa import router as taxa_router
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
            storage_provider=active_settings.storage_provider,
            moderation_provider=active_settings.moderation_provider,
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
    app.include_router(well_known_router)
    app.include_router(groups_router)
    app.include_router(photos_router)
    app.include_router(observations_router)
    app.include_router(dex_router)
    app.include_router(geocode_router)
    app.include_router(review_queue_router)
    app.include_router(expeditions_router)
    app.include_router(sanctuary_router)
    app.include_router(species_router)
    app.include_router(taxa_router)
    return app


app = create_app()
