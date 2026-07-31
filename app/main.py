"""FastAPI application entrypoint: routers, error envelope, middleware."""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.exceptions import ErrorCode, GatewayError, error_envelope
from app.middleware import AccessLogMiddleware, RequestSizeLimitMiddleware
from app.routers import health, inbox, notes, search

logger = logging.getLogger("obsidian_gateway")

app = FastAPI(
    title="Obsidian Vault Gateway",
    description=(
        "Read-mostly REST gateway over an Obsidian vault: full-vault search, note "
        "reads, and note creation restricted to 00_Inbox/ChatGPT."
    ),
    version="0.1.0",
)


@app.exception_handler(GatewayError)
async def handle_gateway_error(_request: Request, exc: GatewayError) -> JSONResponse:
    if exc.status_code >= 500:
        logger.error("gateway_error", extra={"code": exc.code.value, "detail": exc.log_detail})
    elif exc.log_detail:
        logger.info("gateway_error", extra={"code": exc.code.value, "detail": exc.log_detail})
    return JSONResponse(status_code=exc.status_code, content=error_envelope(exc.code, exc.message))


@app.exception_handler(StarletteHTTPException)
async def handle_http_exception(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
    # Any HTTPException not already a GatewayError (404 route-not-found, 405
    # method-not-allowed, ...) still gets the standard envelope.
    code = ErrorCode.NOTE_NOT_FOUND if exc.status_code == 404 else ErrorCode.INTERNAL_ERROR
    message = exc.detail if isinstance(exc.detail, str) else "Request failed."
    return JSONResponse(status_code=exc.status_code, content=error_envelope(code, message))


@app.exception_handler(RequestValidationError)
async def handle_validation_error(
    _request: Request, _exc: RequestValidationError
) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content=error_envelope(ErrorCode.VALIDATION_ERROR, "The request could not be validated."),
    )


@app.exception_handler(Exception)
async def handle_unexpected_error(_request: Request, _exc: Exception) -> JSONResponse:
    # No stack trace, no exception message, in the response — section 13:
    # "内部の絶対パスやスタックトレースは返さない". Full detail goes to the log only.
    logger.exception("unhandled_error")
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=error_envelope(ErrorCode.INTERNAL_ERROR, "An internal error occurred."),
    )


app.add_middleware(AccessLogMiddleware)
app.add_middleware(RequestSizeLimitMiddleware)

app.include_router(health.router, prefix="/api/v1")
app.include_router(search.router, prefix="/api/v1")
app.include_router(notes.router, prefix="/api/v1")
app.include_router(inbox.router, prefix="/api/v1")
