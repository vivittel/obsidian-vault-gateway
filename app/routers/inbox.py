"""GET /api/v1/inbox/duplicate-candidates, POST /api/v1/inbox/notes, and
/inbox/notes/append — IMPLEMENTATION_PLAN section 6.6, PHASE2_PLAN section 6,
and issue #14 / docs/adr/0007-*.md.

Creation's save path is fixed by the API (``settings.inbox_root``); that
request never carries a path, only a title and content. Append is the one
write operation that does take a path — it addresses an *existing* note, and
that path must resolve directly inside the inbox (app/services/
path_security.py's ``resolve_inbox_append_path``) or the request is rejected.
``duplicate-candidates`` is read-only and gates neither write operation — see
its own docstring below.
"""

from __future__ import annotations

from functools import partial
from typing import Annotated
from urllib.parse import quote

import anyio
from fastapi import APIRouter, Depends, Query, Request, Response, status

from app import runtime
from app.application import ApplicationDep
from app.auth import require_token
from app.models import (
    MAX_DUPLICATE_CANDIDATES,
    AppendedNoteResponse,
    CreatedNoteResponse,
    DuplicateCandidatesResponse,
    InboxNoteAppendRequest,
    InboxNoteCreateRequest,
)

router = APIRouter(tags=["inbox"], dependencies=[Depends(require_token)])


@router.get(
    "/inbox/duplicate-candidates",
    response_model=DuplicateCandidatesResponse,
    operation_id="findDuplicateCandidates",
    summary="Find 00_Inbox/ChatGPT notes that may duplicate a proposed title",
)
async def find_duplicate_candidates(
    request: Request,
    application: ApplicationDep,
    title: Annotated[str, Query(min_length=1, max_length=300)],
    project: Annotated[str | None, Query()] = None,
    keywords: Annotated[
        str | None,
        Query(description="Comma-separated keywords, matched against title/tags only."),
    ] = None,
    limit: Annotated[
        int, Query(ge=1, le=MAX_DUPLICATE_CANDIDATES, description="Maximum candidates to return.")
    ] = 5,
) -> DuplicateCandidatesResponse:
    """Read-only: never creates, appends to, or otherwise changes any note.

    ``keywords`` takes the same comma-separated shape ``/search``'s ``tags``
    does; splitting it into a list is this router's job (the MCP tool sends
    a JSON array directly) — folding/deduping happens once, inside
    ``app/services/duplicate_notes.py``, for both transports.
    """
    keyword_list = keywords.split(",") if keywords else None
    response = await anyio.to_thread.run_sync(
        partial(
            application.find_duplicate_candidates,
            title=title,
            project=project,
            keywords=keyword_list,
            limit=limit,
        ),
        limiter=runtime.vault_scan_limiter,
    )
    request.state.result_count = len(response.candidates)
    return response


@router.post(
    "/inbox/notes",
    response_model=CreatedNoteResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="createInboxNote",
    summary="Create a Markdown note in 00_Inbox/ChatGPT",
)
def create_note(
    request: Request,
    body: InboxNoteCreateRequest,
    application: ApplicationDep,
    response: Response,
) -> CreatedNoteResponse:
    if body.export is not None:
        created = application.create_chat_export_note(title=body.title, export=body.export)
    else:
        created = application.create_inbox_note(
            title=body.title,
            content=body.content,
            frontmatter=body.frontmatter,
        )
    request.state.created_note = created.path
    response.headers["Location"] = f"/api/v1/notes?path={quote(created.path, safe='/')}"
    return created


@router.post(
    "/inbox/notes/append",
    response_model=AppendedNoteResponse,
    operation_id="appendInboxNote",
    summary="Append Markdown to an existing note in 00_Inbox/ChatGPT",
)
def append_note(
    request: Request,
    body: InboxNoteAppendRequest,
    application: ApplicationDep,
) -> AppendedNoteResponse:
    appended = application.append_inbox_note(path=body.path, content=body.content)
    request.state.appended_note = appended.path
    return appended
