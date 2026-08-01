"""Transport-neutral application layer (MCP_IMPLEMENTATION_PLAN section 7).

:class:`GatewayApplication` is the one place both the REST routers and, from
Phase 1.5, the MCP tools call into. It depends on nothing HTTP-specific and
nothing MCP-specific — only on :class:`~app.config.Settings` and the existing
services — so the two transports can never observe different behaviour for
the same operation. Anything transport-specific (setting ``request.state``
for the access log, the REST ``Location`` header, MCP tool annotations) stays
in the adapter that calls in here.
"""

from __future__ import annotations

import os
from typing import Annotated

from fastapi import Depends

from app.config import Settings, SettingsDep
from app.exceptions import ValidationError
from app.models import (
    CreatedNoteResponse,
    FrontmatterValue,
    HealthResponse,
    NoteResponse,
    SearchResponse,
    SearchResultItem,
)
from app.services import note_service
from app.services.inbox_service import create_inbox_note
from app.services.search_service import search_notes

MIN_SEARCH_LIMIT = 1
MAX_SEARCH_LIMIT = 200


class GatewayApplication:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def health(self) -> HealthResponse:
        vault_readable = os.access(self.settings.read_root, os.R_OK | os.X_OK)
        inbox_writable = os.access(self.settings.inbox_root, os.W_OK | os.X_OK)
        status = "ok" if vault_readable and inbox_writable else "degraded"
        return HealthResponse(
            status=status,
            vault_readable=vault_readable,
            inbox_writable=inbox_writable,
        )

    def search_notes(
        self,
        *,
        query: str | None = None,
        folder: str | None = None,
        tags: str | None = None,
        limit: int = 20,
    ) -> SearchResponse:
        """Search the vault. ``limit`` is validated against [1, 200] here — not
        just at whichever transport's own parameter validation runs first —
        then clamped to the configured ``MAX_SEARCH_RESULTS`` (U7). Calling
        this directly, as the MCP tool does, gets the same two-stage behaviour
        the REST endpoint has always had.
        """
        if not MIN_SEARCH_LIMIT <= limit <= MAX_SEARCH_LIMIT:
            raise ValidationError(log_detail=f"limit out of range [1, 200]: {limit}")
        effective_limit = min(limit, self.settings.max_search_results)

        hits = search_notes(
            read_root=self.settings.read_root,
            query=query,
            folder=folder,
            tags=tags,
            limit=effective_limit,
            timezone=self.settings.timezone,
            max_note_bytes=self.settings.max_note_size_bytes,
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

    def read_note(self, *, path: str) -> NoteResponse:
        return note_service.read_note(
            path,
            read_root=self.settings.read_root,
            max_note_bytes=self.settings.max_note_size_bytes,
            timezone=self.settings.timezone,
        )

    def create_inbox_note(
        self,
        *,
        title: str,
        content: str,
        frontmatter: dict[str, FrontmatterValue] | None = None,
    ) -> CreatedNoteResponse:
        created = create_inbox_note(
            inbox_root=self.settings.inbox_root,
            title=title,
            content=content,
            frontmatter=frontmatter,
            timezone=self.settings.timezone,
        )
        relative = f"{self.settings.vault_inbox_relative_path}/{created.relative}"
        return CreatedNoteResponse(
            id=relative,
            path=relative,
            title=created.title,
            modified_at=created.modified_at,
        )


def get_application(settings: SettingsDep) -> GatewayApplication:
    return GatewayApplication(settings)


ApplicationDep = Annotated[GatewayApplication, Depends(get_application)]
