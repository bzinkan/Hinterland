"""Structured logging setup."""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Sequence

import structlog
from fastapi import FastAPI, Request, Response
from starlette.middleware.base import RequestResponseEndpoint

from app.core.config import LogLevel, Settings

log = structlog.get_logger()

LOG_LEVELS: dict[LogLevel, int] = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}


def configure_logging(settings: Settings) -> None:
    log_level = LOG_LEVELS[settings.log_level]
    logging.basicConfig(
        format="%(message)s",
        level=log_level,
        force=True,
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def install_request_logging(app: FastAPI) -> None:
    @app.middleware("http")
    async def request_logging_middleware(
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
        request.state.request_id = request_id
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            request_id=request_id,
            method=request.method,
            path=request.url.path,
        )

        started_at = time.perf_counter()
        log.info("http.request_started")
        response = await call_next(request)
        duration_ms = round((time.perf_counter() - started_at) * 1000, 2)
        response.headers["x-request-id"] = request_id
        log.info(
            "http.request_finished",
            status_code=response.status_code,
            duration_ms=duration_ms,
        )
        structlog.contextvars.clear_contextvars()
        return response


def log_observation_event(
    event: str,
    *,
    observation_id: str,
    user_id: str,
    group_id: str | None = None,
    taxon_id: int | None = None,
    handler_rewards: Sequence[str] = (),
    dispatcher_duration_ms: float | None = None,
) -> None:
    """Emit the canonical observation log shape for future submission flows."""
    log.info(
        f"observation.{event}",
        observation_id=observation_id,
        user_id=user_id,
        group_id=group_id,
        taxon_id=taxon_id,
        handler_rewards=list(handler_rewards),
        dispatcher_duration_ms=dispatcher_duration_ms,
    )
