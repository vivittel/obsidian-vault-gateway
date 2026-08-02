"""GET /api/v1/search — IMPLEMENTATION_PLAN section 6.2."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

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
    return application.search_notes(query=q, folder=folder, tags=tags, limit=limit, cursor=cursor)
