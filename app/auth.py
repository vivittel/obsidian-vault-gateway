"""Bearer token authentication (IMPLEMENTATION_PLAN section 4).

``HTTPBearer(auto_error=False)`` is used so that a missing or malformed header
raises our own :class:`~app.exceptions.UnauthorizedError` and therefore gets the
standard ``{"error": {...}}`` envelope instead of FastAPI's ``{"detail": ...}``.
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


async def require_token(settings: SettingsDep, credentials: CredentialsDep) -> None:
    """Reject the request unless it carries the configured bearer token."""
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise UnauthorizedError(log_detail="missing or non-bearer Authorization header")

    # compare_digest on bytes: str comparison raises TypeError for non-ASCII.
    if not secrets.compare_digest(
        credentials.credentials.encode("utf-8"),
        settings.api_token.encode("utf-8"),
    ):
        raise UnauthorizedError(log_detail="bearer token mismatch")
