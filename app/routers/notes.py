"""GET /api/v1/notes — IMPLEMENTATION_PLAN section 6.3.

The path is a query parameter (``?path=...``) rather than a path parameter
(``/notes/{note_id}``) as section 6.3 literally shows. An encoded path
parameter has ambiguous semantics for a slash (``%2F``): ASGI servers,
reverse proxies and the ChatGPT Actions client do not agree on whether it is
decoded before or after routing, which puts uncertainty in front of the path
validator. A query parameter is decoded exactly once by Starlette, so the
value :func:`app.services.path_security.resolve_read_path` sees is the same
value the caller intended — see docs/PHASE1_PLAN.md section 4.5.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from app.auth import require_token
from app.config import SettingsDep
from app.models import NoteResponse
from app.services.markdown_parser import parse_note, read_note_text, to_json_safe
from app.services.path_security import resolve_read_path

router = APIRouter(tags=["notes"], dependencies=[Depends(require_token)])


@router.get(
    "/notes",
    response_model=NoteResponse,
    operation_id="readNote",
    summary="Read a note by its vault-relative path",
)
async def read_note(
    request: Request,
    settings: SettingsDep,
    path: Annotated[
        str, Query(description="Vault-relative path, e.g. 'Knowledge/PC/GPU/RTX 5070.md'.")
    ],
) -> NoteResponse:
    note = resolve_read_path(path, settings.read_root)
    request.state.accessed_note = note.relative
    stat_result = note.path.stat()

    text, truncated = read_note_text(
        note.path,
        size_bytes=stat_result.st_size,
        max_bytes=settings.max_note_size_bytes,
    )
    stem = note.relative.rsplit("/", 1)[-1].removesuffix(".md")
    parsed = parse_note(text, fallback_title=stem)

    return NoteResponse(
        id=note.relative,
        path=note.relative,
        title=parsed.title,
        frontmatter=to_json_safe(parsed.metadata),
        content=parsed.body,
        modified_at=datetime.fromtimestamp(stat_result.st_mtime, tz=settings.timezone),
        truncated=truncated,
    )
