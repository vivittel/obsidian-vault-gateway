"""GET /api/v1/health — IMPLEMENTATION_PLAN section 6.1. No authentication."""

from __future__ import annotations

import os

from fastapi import APIRouter

from app.config import SettingsDep
from app.models import HealthResponse

router = APIRouter(tags=["health"])


@router.get(
    "/health",
    response_model=HealthResponse,
    operation_id="getHealth",
    summary="Report whether the vault mounts are usable",
)
async def get_health(settings: SettingsDep) -> HealthResponse:
    vault_readable = os.access(settings.read_root, os.R_OK | os.X_OK)
    inbox_writable = os.access(settings.inbox_root, os.W_OK | os.X_OK)
    status = "ok" if vault_readable and inbox_writable else "degraded"
    return HealthResponse(
        status=status,
        vault_readable=vault_readable,
        inbox_writable=inbox_writable,
    )
