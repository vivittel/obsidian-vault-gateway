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
from collections.abc import Sequence
from datetime import datetime
from typing import Annotated

from fastapi import Depends

from app.config import Settings, SettingsDep
from app.exceptions import ValidationError
from app.models import (
    MAX_DUPLICATE_CANDIDATES,
    MAX_DUPLICATE_KEYWORDS,
    MAX_RELATED_NOTES,
    AppendedNoteResponse,
    ChatExport,
    CreatedNoteResponse,
    DuplicateCandidate,
    DuplicateCandidatesResponse,
    FrontmatterValue,
    HealthResponse,
    NoteResponse,
    SearchResponse,
    SearchResultItem,
    VaultNameCount,
    VaultSummaryResponse,
    VaultTreeEntry,
    VaultTreeResponse,
)
from app.services import cursor_service, note_service
from app.services.chat_export import render_chat_export
from app.services.duplicate_notes import find_duplicate_candidates as _find_duplicate_candidates
from app.services.inbox_service import append_inbox_note, create_inbox_note
from app.services.related_notes import resolve_related_notes
from app.services.search_service import search_notes
from app.services.vault_service import list_tree, resolve_folder, summarise_vault

MIN_SEARCH_LIMIT = 1
MAX_SEARCH_LIMIT = 200

MIN_TREE_LIMIT = 1
MAX_TREE_LIMIT = 500

MIN_TAGS_LIMIT = 1
MAX_TAGS_LIMIT = 200

MIN_DUPLICATE_LIMIT = 1
DEFAULT_DUPLICATE_LIMIT = 5

SEARCH_OPERATION = "search"
TREE_OPERATION = "tree"


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

    def _cursor_offset(
        self, *, operation: str, conditions: dict[str, object], cursor: str | None
    ) -> tuple[int, str]:
        """Decode ``cursor`` (if given) against a fingerprint of ``conditions``.

        Returns ``(offset, fingerprint)`` — the fingerprint is reused by the
        caller to mint the next page's cursor without recomputing it.
        """
        fingerprint = cursor_service.fingerprint(
            operation=operation, conditions=conditions, api_token=self.settings.api_token
        )
        offset = (
            cursor_service.decode_cursor(
                cursor,
                operation=operation,
                fingerprint=fingerprint,
                api_token=self.settings.api_token,
            )
            if cursor
            else 0
        )
        return offset, fingerprint

    def _next_cursor(
        self, *, operation: str, offset: int, fingerprint: str, has_more: bool
    ) -> str | None:
        if not has_more:
            return None
        return cursor_service.encode_cursor(
            operation=operation,
            offset=offset,
            fingerprint=fingerprint,
            api_token=self.settings.api_token,
        )

    def search_notes(
        self,
        *,
        query: str | None = None,
        folder: str | None = None,
        tags: str | None = None,
        limit: int = 20,
        cursor: str | None = None,
    ) -> SearchResponse:
        """Search the vault. ``limit`` is validated against [1, 200] here — not
        just at whichever transport's own parameter validation runs first —
        then clamped to the configured ``MAX_SEARCH_RESULTS`` (U7). Calling
        this directly, as the MCP tool does, gets the same two-stage behaviour
        the REST endpoint has always had.

        ``cursor`` resumes a previous page. It is bound to ``query``/``folder``/
        ``tags`` (not to ``limit`` — the page size may change between calls
        without invalidating the cursor) via a keyed fingerprint, so a cursor
        minted for one set of conditions is rejected for another.
        """
        if not MIN_SEARCH_LIMIT <= limit <= MAX_SEARCH_LIMIT:
            raise ValidationError(log_detail=f"limit out of range [1, 200]: {limit}")
        effective_limit = min(limit, self.settings.max_search_results)

        conditions = {"query": query or "", "folder": folder or "", "tags": tags or ""}
        offset, fingerprint = self._cursor_offset(
            operation=SEARCH_OPERATION, conditions=conditions, cursor=cursor
        )

        page = search_notes(
            read_root=self.settings.read_root,
            query=query,
            folder=folder,
            tags=tags,
            limit=effective_limit,
            offset=offset,
            timezone=self.settings.timezone,
            max_note_bytes=self.settings.max_note_size_bytes,
        )
        next_cursor = self._next_cursor(
            operation=SEARCH_OPERATION,
            offset=offset + len(page.hits),
            fingerprint=fingerprint,
            has_more=page.has_more,
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
                for hit in page.hits
            ],
            next_cursor=next_cursor,
        )

    def read_note(self, *, path: str) -> NoteResponse:
        return note_service.read_note(
            path,
            read_root=self.settings.read_root,
            max_note_bytes=self.settings.max_note_size_bytes,
            timezone=self.settings.timezone,
        )

    def get_vault_tree(
        self,
        *,
        folder: str | None = None,
        limit: int = 100,
        cursor: str | None = None,
    ) -> VaultTreeResponse:
        """List the direct children of ``folder`` (or the vault root).

        Folders are listed before notes; non-Markdown files are excluded
        entirely. ``cursor`` is bound to the resolved ``folder`` — a cursor
        minted for one folder is rejected for another.
        """
        if not MIN_TREE_LIMIT <= limit <= MAX_TREE_LIMIT:
            raise ValidationError(
                log_detail=f"limit out of range [{MIN_TREE_LIMIT}, {MAX_TREE_LIMIT}]: {limit}"
            )

        # Resolved once: the fingerprint binds to the *resolved* relative
        # path, so "Knowledge" and "Knowledge/" share a cursor, and this
        # avoids resolving the folder a second time to list it.
        resolved = resolve_folder(self.settings.read_root, folder)
        conditions = {"folder": resolved.relative}
        offset, fingerprint = self._cursor_offset(
            operation=TREE_OPERATION, conditions=conditions, cursor=cursor
        )

        page = list_tree(
            resolved=resolved,
            read_root=self.settings.read_root,
            limit=limit,
            offset=offset,
            timezone=self.settings.timezone,
        )
        next_cursor = self._next_cursor(
            operation=TREE_OPERATION,
            offset=offset + len(page.entries),
            fingerprint=fingerprint,
            has_more=page.has_more,
        )
        return VaultTreeResponse(
            folder=page.folder,
            entries=[
                VaultTreeEntry(
                    type=entry.kind,
                    name=entry.name,
                    path=entry.relative,
                    modified_at=entry.modified_at,
                )
                for entry in page.entries
            ],
            next_cursor=next_cursor,
        )

    def get_vault_summary(self, *, top_tags_limit: int = 20) -> VaultSummaryResponse:
        """Aggregate counts, sizes, and tag frequencies over the whole vault.

        Never exposes note bodies, titles, or absolute paths — only counts,
        names, and vault-relative folder labels.
        """
        if not MIN_TAGS_LIMIT <= top_tags_limit <= MAX_TAGS_LIMIT:
            raise ValidationError(
                log_detail=f"top_tags_limit out of range [{MIN_TAGS_LIMIT}, {MAX_TAGS_LIMIT}]: "
                f"{top_tags_limit}"
            )

        stats = summarise_vault(
            read_root=self.settings.read_root,
            top_tags_limit=top_tags_limit,
            timezone=self.settings.timezone,
        )
        return VaultSummaryResponse(
            note_count=stats.note_count,
            total_bytes=stats.total_bytes,
            folder_count=stats.folder_count,
            top_level_folders=[
                VaultNameCount(name=item.name, note_count=item.count)
                for item in stats.top_level_folders
            ],
            tags=[
                VaultNameCount(name=item.name, note_count=item.count) for item in stats.tags
            ],
            last_modified_at=stats.last_modified_at,
            skipped_count=stats.skipped_count,
        )

    def find_duplicate_candidates(
        self,
        *,
        title: str,
        project: str | None = None,
        keywords: Sequence[str] | None = None,
        limit: int = DEFAULT_DUPLICATE_LIMIT,
    ) -> DuplicateCandidatesResponse:
        """Scan ``00_Inbox/ChatGPT`` for notes that may already cover ``title``
        (issue #14; docs/adr/0007-*.md).

        Read-only: never touches ``create_inbox_note``/``append_inbox_note``,
        and this response's ``recommendation`` is advisory only — the Gateway
        does not gate either write tool on it. ``limit``/``keywords`` are
        validated here — not just at whichever transport's own parameter
        validation runs first — matching :meth:`search_notes`'s U7 pattern.
        """
        if not MIN_DUPLICATE_LIMIT <= limit <= MAX_DUPLICATE_CANDIDATES:
            raise ValidationError(
                log_detail=(
                    f"limit out of range [{MIN_DUPLICATE_LIMIT}, "
                    f"{MAX_DUPLICATE_CANDIDATES}]: {limit}"
                )
            )
        if keywords is not None and len(keywords) > MAX_DUPLICATE_KEYWORDS:
            raise ValidationError(
                log_detail=f"more than {MAX_DUPLICATE_KEYWORDS} keywords: {len(keywords)}"
            )

        result = _find_duplicate_candidates(
            read_root=self.settings.read_root,
            inbox_relative_path=self.settings.vault_inbox_relative_path,
            title=title,
            project=project,
            keywords=keywords,
            limit=limit,
            timezone=self.settings.timezone,
        )
        return DuplicateCandidatesResponse(
            candidates=[
                DuplicateCandidate(
                    path=candidate.relative,
                    title=candidate.title,
                    project=candidate.project,
                    tags=list(candidate.tags),
                    confidence=candidate.confidence,
                    matched_signals=list(candidate.matched_signals),
                    matched_keywords=list(candidate.matched_keywords),
                    modified_at=candidate.modified_at,
                )
                for candidate in result.candidates
            ],
            candidate_count=result.candidate_count,
            truncated=result.candidate_count > len(result.candidates),
            recommendation=result.recommendation,
            scanned_count=result.scanned_count,
            skipped_count=result.skipped_count,
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
            related_notes_linked=0,
            related_notes_skipped=0,
        )

    def create_chat_export_note(self, *, title: str, export: ChatExport) -> CreatedNoteResponse:
        """Render a structured chat export and write it via :meth:`create_inbox_note`.

        A second method rather than an extra parameter on
        :meth:`create_inbox_note`: MCP can only ever send ``export`` (never
        ``content``/``frontmatter``), so the "exactly one of" rule is a REST
        request-shape concern that belongs on ``InboxNoteCreateRequest``, not
        here. Both paths still converge on the one
        ``inbox_service.create_inbox_note`` call, which is the invariant that
        actually matters (single write path, single never-overwrite guarantee).

        This is the only place ``datetime.now()`` appears in ``app/`` — the
        formatter (``app.services.chat_export``) stays a pure function of its
        arguments so it can be tested with a fixed clock, and this layer
        already owns ``Settings`` (including the configured timezone).

        ``export.related_notes`` is re-verified against the Vault here, before
        rendering — never inside ``render_chat_export``, which has no
        filesystem access (issue #13; docs/adr/0006-*.md). The verification is
        a handful of ``stat()`` calls at most (``MAX_RELATED_NOTES``), far
        cheaper than a single ``read_note``, so it runs directly on this call
        — no ``anyio.to_thread``, no ``runtime.vault_scan_limiter``; those
        exist to bound full-Vault scans, not this.
        """
        now = datetime.now(tz=self.settings.timezone)
        related = resolve_related_notes(
            export.related_notes, read_root=self.settings.read_root, max_links=MAX_RELATED_NOTES
        )
        rendered = render_chat_export(
            export, title=title, now=now, verified_related_notes=related.links
        )
        created = self.create_inbox_note(
            title=title, content=rendered.content, frontmatter=rendered.frontmatter
        )
        return created.model_copy(
            update={
                "related_notes_linked": len(related.links),
                "related_notes_skipped": related.skipped,
            }
        )

    def append_inbox_note(self, *, path: str, content: str) -> AppendedNoteResponse:
        """Append ``content`` to an existing note directly inside the inbox.

        ``path`` is a full vault-relative path (as returned by ``search_notes``
        /``get_vault_tree``/``read_note``), not a bare file name — the caller
        already knows it in this form, and requiring the inbox prefix here
        (rather than only accepting a bare name) makes the request
        self-describing and matches how every other tool addresses a note.
        """
        appended = append_inbox_note(
            path,
            inbox_root=self.settings.inbox_root,
            inbox_relative_path=self.settings.vault_inbox_relative_path,
            content=content,
            max_note_bytes=self.settings.max_note_size_bytes,
            timezone=self.settings.timezone,
        )
        return AppendedNoteResponse(
            id=appended.relative,
            path=appended.relative,
            modified_at=appended.modified_at,
            appended_bytes=appended.appended_bytes,
        )


def get_application(settings: SettingsDep) -> GatewayApplication:
    return GatewayApplication(settings)


ApplicationDep = Annotated[GatewayApplication, Depends(get_application)]
