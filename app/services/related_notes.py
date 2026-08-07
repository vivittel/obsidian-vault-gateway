"""Verified related-note wikilink resolution (GitHub issue #13;
docs/adr/0006-verified-related-note-wikilinks.md).

The client sends candidate vault-relative paths (``ChatExport.related_notes``)
that it selected from ``search_notes`` results; nothing here trusts that they
still exist, still resolve inside the Vault, or are even syntactically safe to
render as a wikilink. ``resolve_related_notes`` re-verifies every candidate
against the Vault and returns only the survivors, in the client's original
order — like every other service, it takes the root/limits it needs as
arguments rather than reaching for ``Settings`` itself.

A candidate is dropped, never rejected, for any reason short of a
non-negative ``max_links``: unresolvable, wrong file type, hidden, symlinked,
outside the Vault, a wikilink-hazardous filename, or a duplicate of an
earlier survivor. Related-note failures must never block the export (issue
#13's acceptance criteria). An unexpected filesystem error (permissions,
I/O) is not treated as "candidate was invalid" and is allowed to propagate —
see docs/adr/0006-*.md's TOCTOU decision.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from app.exceptions import GatewayError
from app.services.chat_export import is_renderable_wikilink_target
from app.services.path_security import resolve_read_path


@dataclass(frozen=True)
class RelatedNotes:
    links: tuple[str, ...]
    skipped: int


def resolve_related_notes(
    candidates: Sequence[str], *, read_root: Path, max_links: int
) -> RelatedNotes:
    """Re-verify ``candidates`` against the Vault under ``read_root``.

    Candidate paths are never rewritten — not NFC-normalised, not
    whitespace-trimmed, nothing — because rewriting a path can make it name a
    different file than the one the client actually verified via
    ``search_notes``. A candidate either resolves as given, or it is dropped.

    The link count is capped at ``max_links`` survivors, checked at the top
    of the loop with ``>=`` rather than after each append: checking after
    append would let ``max_links=0`` slip through on the first candidate.
    """
    if max_links < 0:
        raise ValueError("max_links must be non-negative")

    links: list[str] = []
    seen: set[str] = set()

    for candidate in candidates:
        if len(links) >= max_links:
            break

        if not is_renderable_wikilink_target(candidate):
            continue

        try:
            note = resolve_read_path(candidate, read_root)
        except (GatewayError, FileNotFoundError):
            # FileNotFoundError: resolve_read_path's own FileNotFoundError
            # handling covers resolve()'s own lookup, but its trailing
            # stat() call is outside that try/except — a note deleted
            # between the two (a real possibility under LiveSync) would
            # otherwise raise past this function uncaught. Every other
            # OSError (PermissionError, EIO, ...) is not a "candidate was
            # invalid" condition and is left to propagate.
            continue

        if note.relative in seen:
            # Identity is the vault-relative path, not the inode: two
            # distinct paths that happen to be hardlinked are two distinct
            # notes in Obsidian's namespace (two search_notes results, two
            # graph nodes), and collapsing them would silently drop a link
            # the client legitimately selected.
            continue

        seen.add(note.relative)
        links.append(note.relative)

    return RelatedNotes(links=tuple(links), skipped=len(candidates) - len(links))
