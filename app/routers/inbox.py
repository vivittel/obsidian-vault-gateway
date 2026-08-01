"""POST /api/v1/inbox/notes — IMPLEMENTATION_PLAN section 6.6.

The save path is fixed by the API (``settings.inbox_root``); the request never
carries a path, only a title and content.
"""

from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, Request, Response, status

from app.application import ApplicationDep
from app.auth import require_token
from app.models import CreatedNoteResponse, InboxNoteCreateRequest

router = APIRouter(tags=["inbox"], dependencies=[Depends(require_token)])


@router.post(
    "/inbox/notes",
    response_model=CreatedNoteResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="createInboxNote",
    summary="Create a Markdown note in 00_Inbox/ChatGPT",
)
async def create_note(
    request: Request,
    body: InboxNoteCreateRequest,
    application: ApplicationDep,
    response: Response,
) -> CreatedNoteResponse:
    created = application.create_inbox_note(
        title=body.title,
        content=body.content,
        frontmatter=body.frontmatter,
    )
    request.state.created_note = created.path
    response.headers["Location"] = f"/api/v1/notes?path={quote(created.path, safe='/')}"
    return created
