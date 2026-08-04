"""GET /api/v1/notes — IMPLEMENTATION_PLAN section 6.3.

The path is a query parameter (``?path=...``) rather than a path parameter
(``/notes/{note_id}``) as section 6.3 literally shows. An encoded path
parameter has ambiguous semantics for a slash (``%2F``): ASGI servers,
reverse proxies and HTTP clients generally do not agree on whether it is
decoded before or after routing, which puts uncertainty in front of the path
validator. A query parameter is decoded exactly once by Starlette, so the
value :func:`app.services.path_security.resolve_read_path` sees is the same
value the caller intended — see docs/PHASE1_PLAN.md section 4.5.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from app.application import ApplicationDep
from app.auth import require_token
from app.models import NoteResponse

router = APIRouter(tags=["notes"], dependencies=[Depends(require_token)])


@router.get(
    "/notes",
    response_model=NoteResponse,
    operation_id="readNote",
    summary="Read a note by its vault-relative path",
)
def read_note(
    request: Request,
    application: ApplicationDep,
    path: Annotated[
        str, Query(description="Vault-relative path, e.g. 'Knowledge/PC/GPU/RTX 5070.md'.")
    ],
) -> NoteResponse:
    response = application.read_note(path=path)
    request.state.accessed_note = response.path
    return response
