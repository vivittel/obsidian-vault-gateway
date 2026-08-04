"""GET /api/v1/vault/tree and /vault/summary — PHASE2_PLAN sections 3-4."""

from __future__ import annotations

from functools import partial
from typing import Annotated

import anyio
from fastapi import APIRouter, Depends, Query, Request

from app.application import ApplicationDep
from app.auth import require_token
from app.models import VaultSummaryResponse, VaultTreeResponse

router = APIRouter(tags=["vault"], dependencies=[Depends(require_token)])


@router.get(
    "/vault/tree",
    response_model=VaultTreeResponse,
    operation_id="getVaultTree",
    summary="List the direct children of a vault folder",
)
def get_vault_tree(
    application: ApplicationDep,
    folder: Annotated[
        str | None, Query(description="Vault-relative folder to list. Omit for the vault root.")
    ] = None,
    limit: Annotated[
        int, Query(ge=1, le=500, description="Maximum number of entries.")
    ] = 100,
    cursor: Annotated[
        str | None, Query(description="Opaque pagination token from a previous response.")
    ] = None,
) -> VaultTreeResponse:
    # Not a full-vault scan — app/services/path_security.py's iter_directory
    # lists one directory level via a single scandir(), so this stays a
    # plain sync `def` and FastAPI's default thread pool (app/routers/
    # search.py's dedicated limiter is only for the two handlers below that
    # actually walk the whole vault).
    return application.get_vault_tree(folder=folder, limit=limit, cursor=cursor)


@router.get(
    "/vault/summary",
    response_model=VaultSummaryResponse,
    operation_id="getVaultSummary",
    summary="Summarise vault-wide note counts, sizes, and tags",
)
async def get_vault_summary(
    request: Request,
    application: ApplicationDep,
    top_tags_limit: Annotated[
        int, Query(ge=1, le=200, description="Maximum number of tags to return.")
    ] = 20,
) -> VaultSummaryResponse:
    # A full-vault scan — see app/routers/search.py's identical comment.
    return await anyio.to_thread.run_sync(
        partial(application.get_vault_summary, top_tags_limit=top_tags_limit),
        limiter=request.app.state.vault_scan_limiter,
    )
