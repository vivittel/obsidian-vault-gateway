"""Bearer token authentication (IMPLEMENTATION_PLAN section 4, section 10).

``HTTPBearer(auto_error=False)`` is used so that a missing or malformed header
raises our own :class:`~app.exceptions.UnauthorizedError` and therefore gets the
standard ``{"error": {...}}`` envelope instead of FastAPI's ``{"detail": ...}``.

:func:`verify_bearer_token` is the transport-neutral half: it holds the actual
comparison and nothing else, so the MCP ASGI middleware (app/mcp_auth.py) can
share it with the REST dependency below instead of re-implementing
``compare_digest`` against a second copy of the token.
"""

from __future__ import annotations

import secrets
from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import SettingsDep
from app.exceptions import UnauthorizedError

bearer_scheme = HTTPBearer(
    auto_error=False,
    scheme_name="bearerAuth",
    description="Send `Authorization: Bearer <API_TOKEN>`.",
)

CredentialsDep = Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)]


def verify_bearer_token(*, provided: str, expected: str) -> bool:
    """Constant-time comparison of a caller-supplied token against the configured one.

    Both sides are encoded to bytes first: ``secrets.compare_digest`` raises
    ``TypeError`` when given two ``str`` operands that aren't both ASCII, and a
    caller can send a non-ASCII bearer token.
    """
    return secrets.compare_digest(provided.encode("utf-8"), expected.encode("utf-8"))


async def require_token(settings: SettingsDep, credentials: CredentialsDep) -> None:
    """Reject the request unless it carries the configured bearer token."""
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise UnauthorizedError(log_detail="missing or non-bearer Authorization header")

    if not verify_bearer_token(provided=credentials.credentials, expected=settings.api_token):
        raise UnauthorizedError(log_detail="bearer token mismatch")
