"""API error response conventions."""

from __future__ import annotations

from collections.abc import Sequence
from http import HTTPStatus
from typing import cast

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

log = structlog.get_logger()


class ErrorBody(BaseModel):
    code: str
    message: str
    request_id: str | None = None
    details: Sequence[object] | dict[str, object] | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody


def _request_id(request: Request) -> str | None:
    value = getattr(request.state, "request_id", None)
    return value if isinstance(value, str) else None


def _error_content(
    *,
    code: str,
    message: str,
    request_id: str | None,
    details: Sequence[object] | dict[str, object] | None = None,
) -> dict[str, object]:
    response = ErrorResponse(
        error=ErrorBody(
            code=code,
            message=message,
            request_id=request_id,
            details=details,
        )
    )
    return cast(dict[str, object], response.model_dump(mode="json"))


def install_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(
        request: Request,
        exc: StarletteHTTPException,
    ) -> JSONResponse:
        status = HTTPStatus(exc.status_code)
        code = "not_found" if exc.status_code == 404 else "http_error"
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_content(
                code=code,
                message=str(exc.detail or status.phrase),
                request_id=_request_id(request),
            ),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=_error_content(
                code="validation_error",
                message="Request validation failed",
                request_id=_request_id(request),
                details=exc.errors(),
            ),
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(
        request: Request,
        exc: Exception,
    ) -> JSONResponse:
        log.exception("api.unhandled_exception", error=str(exc))
        return JSONResponse(
            status_code=500,
            content=_error_content(
                code="internal_server_error",
                message="Internal server error",
                request_id=_request_id(request),
            ),
        )
