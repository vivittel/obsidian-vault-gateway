"""POST /api/v1/inbox/notes and /inbox/notes/append — IMPLEMENTATION_PLAN
section 6.6 and PHASE2_PLAN section 6.

Creation's save path is fixed by the API (``settings.inbox_root``); that
request never carries a path, only a title and content. Append is the one
write operation that does take a path — it addresses an *existing* note, and
that path must resolve directly inside the inbox (app/services/
path_security.py's ``resolve_inbox_append_path``) or the request is rejected.
"""

from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, Request, Response, status

from app.application import ApplicationDep
from app.auth import require_token
from app.models import (
    AppendedNoteResponse,
    CreatedNoteResponse,
    InboxNoteAppendRequest,
    InboxNoteCreateRequest,
)

router = APIRouter(tags=["inbox"], dependencies=[Depends(require_token)])


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
