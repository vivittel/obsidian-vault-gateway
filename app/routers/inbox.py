"""POST /api/v1/inbox/notes — IMPLEMENTATION_PLAN section 6.6.

The save path is fixed by the API (``settings.inbox_root``); the request never
carries a path, only a title and content.
"""

from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, Request, Response, status

from app.auth import require_token
from app.config import SettingsDep
from app.models import CreatedNoteResponse, InboxNoteCreateRequest
from app.services.inbox_service import create_inbox_note

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
    settings: SettingsDep,
    response: Response,
) -> CreatedNoteResponse:
    created = create_inbox_note(
        inbox_root=settings.inbox_root,
        title=body.title,
        content=body.content,
        frontmatter=body.frontmatter,
        timezone=settings.timezone,
    )
    relative = f"{settings.vault_inbox_relative_path}/{created.relative}"
    request.state.created_note = relative
    response.headers["Location"] = f"/api/v1/notes?path={quote(relative, safe='/')}"

    return CreatedNoteResponse(
        id=relative,
        path=relative,
        title=created.title,
        modified_at=created.modified_at,
    )
