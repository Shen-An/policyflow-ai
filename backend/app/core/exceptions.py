"""Application exceptions and FastAPI exception handlers."""

import json
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from backend.app.core.logging import get_logger

logger = get_logger(__name__)


class ApplicationError(Exception):
    """Base exception for expected application failures."""

    def __init__(
        self,
        code: str,
        message: str,
        status_code: int = status.HTTP_400_BAD_REQUEST,
        details: Any | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details


class NotFoundError(ApplicationError):
    def __init__(self, message: str = "Resource not found", details: Any | None = None) -> None:
        super().__init__("NOT_FOUND", message, status.HTTP_404_NOT_FOUND, details)


class PermissionDeniedError(ApplicationError):
    def __init__(self, message: str = "Permission denied", details: Any | None = None) -> None:
        super().__init__("PERMISSION_DENIED", message, status.HTTP_403_FORBIDDEN, details)


class AuthenticationError(ApplicationError):
    def __init__(
        self,
        code: str = "AUTHENTICATION_REQUIRED",
        message: str = "Authentication required",
        details: Any | None = None,
    ) -> None:
        super().__init__(code, message, status.HTTP_401_UNAUTHORIZED, details)


class ConflictError(ApplicationError):
    def __init__(self, code: str, message: str, details: Any | None = None) -> None:
        super().__init__(code, message, status.HTTP_409_CONFLICT, details)


def _request_id(request: Request) -> str | None:
    value = getattr(request.state, "request_id", None)
    return str(value) if value else None


def _error_payload(
    request: Request,
    code: str,
    message: str,
    details: Any | None = None,
) -> dict[str, Any]:
    return {
        "success": False,
        "error": {"code": code, "message": message, "details": details},
        "request_id": _request_id(request),
    }


def _json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str))


def unexpected_exception_response(request: Request, exc: Exception) -> JSONResponse:
    logger.exception(
        "Unhandled application exception",
        extra={
            "request_path": request.url.path,
            "request_method": request.method,
            "error_code": "INTERNAL_ERROR",
            "status_code": status.HTTP_500_INTERNAL_SERVER_ERROR,
        },
        exc_info=exc,
    )
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=_error_payload(request, "INTERNAL_ERROR", "Internal server error"),
    )


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApplicationError)
    async def handle_app_exception(request: Request, exc: ApplicationError) -> JSONResponse:
        logger.warning(
            "Application request failed",
            extra={
                "request_path": request.url.path,
                "request_method": request.method,
                "error_code": exc.code,
                "status_code": exc.status_code,
            },
        )
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_payload(request, exc.code, exc.message, exc.details),
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        logger.warning(
            "Request validation failed",
            extra={
                "request_path": request.url.path,
                "request_method": request.method,
                "error_code": "VALIDATION_ERROR",
                "status_code": status.HTTP_422_UNPROCESSABLE_CONTENT,
            },
        )
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content=_error_payload(
                request,
                "VALIDATION_ERROR",
                "Request validation failed",
                _json_safe(exc.errors()),
            ),
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_exception(
        request: Request,
        exc: StarletteHTTPException,
    ) -> JSONResponse:
        logger.warning(
            "HTTP request failed",
            extra={
                "request_path": request.url.path,
                "request_method": request.method,
                "error_code": "HTTP_ERROR",
                "status_code": exc.status_code,
            },
        )
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_payload(request, "HTTP_ERROR", str(exc.detail)),
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_exception(request: Request, exc: Exception) -> JSONResponse:
        return unexpected_exception_response(request, exc)
