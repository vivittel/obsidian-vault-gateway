"""GET /api/v1/search — IMPLEMENTATION_PLAN section 6.2."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.auth import require_token
from app.config import SettingsDep
from app.models import SearchResponse, SearchResultItem
from app.services.search_service import search_notes

router = APIRouter(tags=["search"], dependencies=[Depends(require_token)])


@router.get(
    "/search",
    response_model=SearchResponse,
    operation_id="searchNotes",
    summary="Search the vault by file name, title, tags, headings and body",
)
async def search(
    settings: SettingsDep,
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
) -> SearchResponse:
    hits = search_notes(
        read_root=settings.read_root,
        query=q,
        folder=folder,
        tags=tags,
        limit=min(limit, settings.max_search_results),
        timezone=settings.timezone,
        max_note_bytes=settings.max_note_size_bytes,
    )
    return SearchResponse(
        results=[
            SearchResultItem(
                id=hit.relative,
                path=hit.relative,
                title=hit.title,
                excerpt=hit.excerpt,
                tags=hit.tags,
                modified_at=hit.modified_at,
            )
            for hit in hits
        ],
        next_cursor=None,
    )
