"""API error response conventions."""

from __future__ import annotations

from collections.abc import Sequence
from http import HTTPStatus
from typing import cast

import structlog
from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
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


def api_error_detail(
    code: str,
    message: str,
    *,
    details: Sequence[object] | dict[str, object] | None = None,
) -> dict[str, object]:
    """Build a structured HTTPException detail understood by our handler."""
    value: dict[str, object] = {"code": code, "message": message}
    if details is not None:
        value["details"] = details
    return value


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


def _validation_details(exc: RequestValidationError) -> Sequence[object]:
    """Return JSON-safe Pydantic errors.

    Model-validator failures place the original ``ValueError`` in ``ctx``.
    Passing that object into ``model_dump(mode="json")`` raises while trying
    to report the client error, turning a valid 422 into a 500.
    """
    normalized: list[object] = []
    for raw_error in exc.errors():
        error = dict(raw_error)
        context = error.get("ctx")
        if isinstance(context, dict):
            error["ctx"] = {
                key: str(value) if isinstance(value, BaseException) else value
                for key, value in context.items()
            }
        normalized.append(jsonable_encoder(error))
    return normalized


def install_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(
        request: Request,
        exc: StarletteHTTPException,
    ) -> JSONResponse:
        status = HTTPStatus(exc.status_code)
        code = "not_found" if exc.status_code == 404 else "http_error"
        message = str(exc.detail or status.phrase)
        details: Sequence[object] | dict[str, object] | None = None
        if isinstance(exc.detail, dict):
            explicit_code = exc.detail.get("code")
            explicit_message = exc.detail.get("message")
            explicit_details = exc.detail.get("details")
            if isinstance(explicit_code, str) and isinstance(explicit_message, str):
                code = explicit_code
                message = explicit_message
                if isinstance(explicit_details, (list, dict)):
                    details = explicit_details
        return JSONResponse(
            status_code=exc.status_code,
            headers=exc.headers,
            content=_error_content(
                code=code,
                message=message,
                request_id=_request_id(request),
                details=details,
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
                details=_validation_details(exc),
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
