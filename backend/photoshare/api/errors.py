"""
Exception -> HTTP status/ErrorResponse mapping (v3.0 §2.7/§5.3,
How It Works Guide §10). Registered once on the FastAPI app in main.py so
every route gets consistent error envelopes without repeating try/except
in each handler.
"""
from __future__ import annotations

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from photoshare.api.models import ErrorResponse
from photoshare.services.metadata_repository import NotFoundError
from photoshare.services.thumbnail_service import InvalidThumbnailSizeError
from photoshare.storage.errors import (
    FilesystemPermissionError,
    InvalidRootPathError,
    MalformedPathError,
    PathOutsideRootError,
)


def _json_error(status_code: int, code: str, message: str, detail=None) -> JSONResponse:
    body = ErrorResponse(code=code, message=message, detail=detail)
    return JSONResponse(status_code=status_code, content=body.model_dump(mode="json"))


async def not_found_handler(request: Request, exc: NotFoundError) -> JSONResponse:
    return _json_error(404, exc.code, exc.message)


async def path_outside_root_handler(request: Request, exc: PathOutsideRootError) -> JSONResponse:
    return _json_error(403, "PATH_OUTSIDE_ROOT", str(exc))


async def malformed_path_handler(request: Request, exc: MalformedPathError) -> JSONResponse:
    return _json_error(400, "MALFORMED_PATH", str(exc))


async def filesystem_permission_handler(
    request: Request, exc: FilesystemPermissionError
) -> JSONResponse:
    return _json_error(500, "FILESYSTEM_PERMISSION_ERROR", str(exc))


async def invalid_thumbnail_size_handler(
    request: Request, exc: InvalidThumbnailSizeError
) -> JSONResponse:
    return _json_error(400, "INVALID_THUMBNAIL_SIZE", str(exc))


async def invalid_root_path_handler(
    request: Request, exc: InvalidRootPathError
) -> JSONResponse:
    return _json_error(400, "INVALID_ROOT_PATH", str(exc))


async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Normalizes FastAPI/Pydantic 422s into the same ErrorResponse shape
    (How It Works Guide §10) so API consumers never handle two envelope
    shapes. `detail` carries the field-level validation errors."""
    return _json_error(422, "VALIDATION_ERROR", "Request validation failed", detail=exc.errors())


async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """Catches HTTPException raised by the auth dependency (401 AUTH_MISSING
    / AUTH_INVALID), whose `detail` is already a {code, message} dict."""
    if isinstance(exc.detail, dict) and "code" in exc.detail and "message" in exc.detail:
        return _json_error(exc.status_code, exc.detail["code"], exc.detail["message"])
    return _json_error(exc.status_code, "HTTP_ERROR", str(exc.detail))


def register_exception_handlers(app) -> None:
    app.add_exception_handler(NotFoundError, not_found_handler)
    app.add_exception_handler(PathOutsideRootError, path_outside_root_handler)
    app.add_exception_handler(MalformedPathError, malformed_path_handler)
    app.add_exception_handler(FilesystemPermissionError, filesystem_permission_handler)
    app.add_exception_handler(InvalidThumbnailSizeError, invalid_thumbnail_size_handler)
    app.add_exception_handler(InvalidRootPathError, invalid_root_path_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
