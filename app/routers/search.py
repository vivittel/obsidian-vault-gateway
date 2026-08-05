"""GET /api/v1/search — IMPLEMENTATION_PLAN section 6.2."""

from __future__ import annotations

from functools import partial
from typing import Annotated

import anyio
from fastapi import APIRouter, Depends, Query, Request

from app import runtime
from app.application import ApplicationDep
from app.auth import require_token
from app.models import SearchResponse

router = APIRouter(tags=["search"], dependencies=[Depends(require_token)])


@router.get(
    "/search",
    response_model=SearchResponse,
    operation_id="searchNotes",
    summary="Search the vault by file name, title, tags, headings and body",
)
async def search(
    request: Request,
    application: ApplicationDep,
    q: Annotated[str | None, Query(description="Free-text search term.")] = None,
    folder: Annotated[
        str | None, Query(description="Vault-relative folder to restrict the search to.")
    ] = None,
    tags: Annotated[
        str | None, Query(description="Comma-separated frontmatter tags; all must match.")
    ] = None,
    limit: Annotated[
        int, Query(ge=1, le=200, description="Maximum number of results.")
    ] = 20,
    cursor: Annotated[
        str | None, Query(description="Opaque pagination token from a previous response.")
    ] = None,
) -> SearchResponse:
    # A full-vault scan (app/services/search_service.py) — run in a worker
    # thread through a dedicated limiter (app/runtime.py) shared with MCP's
    # search_notes tool, instead of as a plain `def` endpoint sharing
    # FastAPI's default thread pool, so this can never starve /health or any
    # other lightweight request (on either transport) of a thread.
    response = await anyio.to_thread.run_sync(
        partial(
            application.search_notes, query=q, folder=folder, tags=tags, limit=limit, cursor=cursor
        ),
        limiter=runtime.vault_scan_limiter,
    )
    # Picked up by AccessLogMiddleware — IMPLEMENTATION_PLAN section 14's
    # "結果件数". Same request.state hand-off app/routers/notes.py uses for
    # note paths, so the middleware never has to inspect the response body.
    request.state.result_count = len(response.results)
    return response
