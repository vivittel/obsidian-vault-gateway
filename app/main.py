"""ASGI entrypoint: the REST FastAPI app, the /mcp Streamable HTTP transport,
and the top-level composition that combines them (MCP_IMPLEMENTATION_PLAN
sections 9 and 15).

``app`` — the object Dockerfile's ``uvicorn app.main:app`` and
``docker-compose``'s healthcheck expect unchanged (MCP section 22: "CMDは既存
Uvicornのまま") — is a plain :class:`starlette.applications.Starlette` that
mounts two peer ASGI apps:

* ``/mcp``  → the Streamable HTTP transport, wrapped in bearer auth
* ``/``     → ``rest_app``, the FastAPI application below, unchanged

Mounting them as siblings on a *third*, otherwise-empty Starlette instance —
rather than ``rest_app.mount("/mcp", ...)`` on the FastAPI app itself — is
deliberate: ``rest_app``'s own exception handlers
(``handle_gateway_error``/``handle_http_exception``/``handle_unexpected_error``
below) are installed via Starlette's ``ExceptionMiddleware``, which wraps
*that app's own router*. A request never reaches ``rest_app``'s router at
all unless it fails ``/mcp``'s own ``Mount`` match first, so those handlers
can never see, and can never rewrite, anything from the MCP transport — the
exact property MCP_IMPLEMENTATION_PLAN section 15 asks to have "担保"
(guaranteed), not just usually true.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import AsyncIterator

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.applications import Starlette
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.routing import Mount, Route

from app.config import API_PREFIX, get_settings
from app.exceptions import ErrorCode, GatewayError, error_envelope
from app.mcp_server import build_mcp_transport, mcp
from app.middleware import AccessLogMiddleware, RequestSizeLimitMiddleware
from app.routers import health, inbox, notes, search

logger = logging.getLogger("obsidian_gateway")

rest_app = FastAPI(
    title="Obsidian Vault Gateway",
    description=(
        "Read-mostly gateway over an Obsidian vault: full-vault search, note "
        "reads, and note creation restricted to 00_Inbox/ChatGPT. MCP "
        "(mounted at /mcp) is the primary interface; this REST API is kept "
        "for health checks, curl-based diagnostics, and regression tests."
    ),
    version="0.1.0",
)


@rest_app.exception_handler(GatewayError)
async def handle_gateway_error(_request: Request, exc: GatewayError) -> JSONResponse:
    if exc.status_code >= 500:
        logger.error("gateway_error", extra={"code": exc.code.value, "detail": exc.log_detail})
    elif exc.log_detail:
        logger.info("gateway_error", extra={"code": exc.code.value, "detail": exc.log_detail})
    return JSONResponse(status_code=exc.status_code, content=error_envelope(exc.code, exc.message))


@rest_app.exception_handler(StarletteHTTPException)
async def handle_http_exception(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
    # Any HTTPException not already a GatewayError (404 route-not-found, 405
    # method-not-allowed, ...) still gets the standard envelope.
    code = ErrorCode.NOTE_NOT_FOUND if exc.status_code == 404 else ErrorCode.INTERNAL_ERROR
    message = exc.detail if isinstance(exc.detail, str) else "Request failed."
    return JSONResponse(status_code=exc.status_code, content=error_envelope(code, message))


@rest_app.exception_handler(RequestValidationError)
async def handle_validation_error(
    _request: Request, _exc: RequestValidationError
) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content=error_envelope(ErrorCode.VALIDATION_ERROR, "The request could not be validated."),
    )


@rest_app.exception_handler(Exception)
async def handle_unexpected_error(_request: Request, _exc: Exception) -> JSONResponse:
    # No stack trace, no exception message, in the response — section 13:
    # "内部の絶対パスやスタックトレースは返さない". Full detail goes to the log only.
    logger.exception("unhandled_error")
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=error_envelope(ErrorCode.INTERNAL_ERROR, "An internal error occurred."),
    )


rest_app.add_middleware(AccessLogMiddleware)
rest_app.add_middleware(RequestSizeLimitMiddleware)

rest_app.include_router(health.router, prefix=API_PREFIX)
rest_app.include_router(search.router, prefix=API_PREFIX)
rest_app.include_router(notes.router, prefix=API_PREFIX)
rest_app.include_router(inbox.router, prefix=API_PREFIX)


# --- MCP: built once at import time --------------------------------------
#
# mcp.session_manager (used by the combined lifespan below) only exists
# after streamable_http_app() has been called at least once — accessing it
# any earlier raises RuntimeError by design (verified against the installed
# SDK). Building the transport here, at module load, is what MCP
# _IMPLEMENTATION_PLAN section 9's "モジュール読み込み時にappを作り、lifespan
# では生成済みを使う" ordering requires, and it means every setting the
# transport bakes in (DNS-rebinding allowlist, request body cap) is fixed
# for the process's lifetime — correct for a long-running server, and the
# reason tests that need a specific value for either one use the same
# MCP_ALLOWED_HOSTS/MAX_REQUEST_BYTES the shared `env` fixture already sets,
# rather than varying it per test the way REST's Settings-via-DI allows.
#
# build_mcp_transport() must be called on `mcp` exactly once for the whole
# process — see its docstring in app/mcp_server.py. Tests that need their
# own independent lifespan build their own throwaway MCPServer instead of
# calling this again here.
_mcp_app_with_auth = build_mcp_transport(mcp, get_settings())


@contextlib.asynccontextmanager
async def _lifespan(_app: Starlette) -> AsyncIterator[None]:
    async with contextlib.AsyncExitStack() as stack:
        # rest_app currently registers no startup/shutdown behaviour of its
        # own, but entering its lifespan here — rather than skipping it — is
        # what keeps this composition correct if it ever gains any.
        await stack.enter_async_context(rest_app.router.lifespan_context(rest_app))
        await stack.enter_async_context(mcp.session_manager.run())
        yield


async def _redirect_bare_mcp_path(_request: Request) -> RedirectResponse:
    # Mount("/mcp", ...) below can only ever match "/mcp/..." — Starlette
    # builds its path regex as f"{path}/{{path:path}}", so the bare "/mcp"
    # (no trailing slash) structurally never matches it (confirmed against
    # Mount.matches() directly). Without this explicit route, an outer
    # Router's usual redirect-slash fallback never gets a chance to fire
    # either: Mount("/", app=rest_app) below greedily matches "/mcp" first
    # (it matches *any* path), so the request would silently fall through to
    # a REST 404 instead of reaching the MCP transport at all.
    return RedirectResponse(url="/mcp/", status_code=307)


app = Starlette(
    routes=[
        # Order matters: the bare-path redirect and /mcp mount must both be
        # listed before the catch-all "/" mount, or the latter — which
        # matches any path — would claim "/mcp" first.
        Route("/mcp", endpoint=_redirect_bare_mcp_path, methods=["GET", "POST", "DELETE"]),
        Mount("/mcp", app=_mcp_app_with_auth),
        Mount("/", app=rest_app),
    ],
    lifespan=_lifespan,
)
