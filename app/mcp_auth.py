"""Bearer authentication for the ``/mcp`` Streamable HTTP endpoint
(MCP_IMPLEMENTATION_PLAN section 8, section 17; ADR-0001 security invariants).

REST's ``Depends(require_token)`` only runs for FastAPI *routes* — a mounted
ASGI sub-application (the MCP transport) never goes through FastAPI's
dependency injection at all. This middleware wraps the MCP sub-app *before*
it is mounted in app/main.py, so every HTTP request reaching it is checked
regardless of which JSON-RPC method the body names — including the
2026-07-28 spec's ``server/discover`` and the legacy ``initialize`` (MCP
section 8: "``/mcp``はinitializeを含む全リクエストで認証必須"). Passthrough
is unconditional for anything that isn't an HTTP request, and this never
wraps ``send``/``receive`` on the authorized path, so it adds no buffering in
front of the Streamable HTTP transport's SSE responses.

No CORS handling is added here: this server is not reached by a browser
(section 15, "CORS不要であること") — an OPTIONS request is simply another
HTTP request that gets the same 401 without a valid bearer token.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from starlette.datastructures import Headers

from app.auth import verify_bearer_token
from app.config import get_settings

if TYPE_CHECKING:
    from starlette.types import Receive, Scope, Send

logger = logging.getLogger("obsidian_gateway")
mcp_access_logger = logging.getLogger("obsidian_gateway.mcp")

_UNAUTHORIZED_MESSAGE = "A valid bearer token is required."


class McpBearerAuthMiddleware:
    """Reject any HTTP request to the wrapped MCP app without a valid bearer token.

    A no-op when ``Settings.auth_enabled`` is false (``AUTH_ENABLED=false``):
    every request is passed straight through without inspecting the
    Authorization header at all.
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        settings = get_settings()
        if not settings.auth_enabled:
            await self.app(scope, receive, send)
            return

        authorization = Headers(scope=scope).get("authorization")
        scheme, _, credential = (authorization or "").partition(" ")

        if scheme.lower() != "bearer" or not credential:
            await self._reject(send, reason="missing_or_non_bearer_authorization_header")
            return

        if not verify_bearer_token(provided=credential, expected=settings.api_token):
            await self._reject(send, reason="bearer_token_mismatch")
            return

        await self.app(scope, receive, send)

    async def _reject(self, send: Send, *, reason: str) -> None:
        # MCP_IMPLEMENTATION_PLAN section 16's dedicated shape for this case
        # ("認証失敗: transport=mcp / status=unauthorized") — `reason` is a
        # fixed enum-like label, never the caller-supplied token.
        mcp_access_logger.info(
            "mcp_auth_failed",
            extra={"transport": "mcp", "status": "unauthorized", "reason": reason},
        )

        # Modeled on the SDK's own RequireAuthMiddleware (mcp.server.auth) so a
        # real MCP client's Bearer-challenge handling recognizes the shape,
        # without pulling in the SDK's OAuth-specific TokenVerifier machinery
        # for what is here a single static token.
        body = json.dumps(
            {"error": "invalid_token", "error_description": _UNAUTHORIZED_MESSAGE}
        ).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": 401,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("ascii")),
                    (b"www-authenticate", b'Bearer error="invalid_token"'),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
