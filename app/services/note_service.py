"""Note reading (IMPLEMENTATION_PLAN sections 6.3 and 8).

Split out of ``routers/notes.py`` so the read path is transport-neutral: this
is the one place both the REST router and, from Phase 1.5, the MCP
``read_note`` tool call to turn a caller-supplied vault-relative path into a
:class:`~app.models.NoteResponse`. Like every other service, it takes the
root/limits it needs as arguments rather than reaching for ``Settings``
itself (see app/config.py's docstring).
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app.models import NoteResponse
from app.services.markdown_parser import parse_note, read_note_text, to_json_safe
from app.services.path_security import resolve_read_path


def read_note(
    raw_path: str,
    *,
    read_root: Path,
    max_note_bytes: int,
    timezone: ZoneInfo,
) -> NoteResponse:
    """Resolve, read and parse a note. Raises a :class:`~app.exceptions.GatewayError`
    subclass (via :func:`resolve_read_path`) for any invalid or missing path.
    """
    note = resolve_read_path(raw_path, read_root)
    stat_result = note.path.stat()

    text, truncated = read_note_text(
        note.path,
        size_bytes=stat_result.st_size,
        max_bytes=max_note_bytes,
    )
    stem = note.relative.rsplit("/", 1)[-1].removesuffix(".md")
    parsed = parse_note(text, fallback_title=stem)

    return NoteResponse(
        id=note.relative,
        path=note.relative,
        title=parsed.title,
        frontmatter=to_json_safe(parsed.metadata),
        content=parsed.body,
        modified_at=datetime.fromtimestamp(stat_result.st_mtime, tz=timezone),
        truncated=truncated,
    )
