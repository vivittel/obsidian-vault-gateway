"""GET /api/v1/health — IMPLEMENTATION_PLAN section 6.1. No authentication."""

from __future__ import annotations

from fastapi import APIRouter

from app.application import ApplicationDep
from app.models import HealthResponse

router = APIRouter(tags=["health"])


@router.get(
    "/health",
    response_model=HealthResponse,
    operation_id="getHealth",
    summary="Report whether the vault mounts are usable",
)
def get_health(application: ApplicationDep) -> HealthResponse:
    return application.health()
