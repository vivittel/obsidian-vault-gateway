"""Vault-structure queries: the tree (PHASE2_PLAN section 3) and the summary (section 4).

Both operations are read-only and never expose note bodies, titles, or
absolute host paths — only vault-relative paths, names, and aggregate counts.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Literal, NamedTuple
from zoneinfo import ZoneInfo

from app.services import markdown_parser
from app.services.path_security import (
    ResolvedNote,
    WalkStats,
    iter_directory,
    iter_vault_notes,
    resolve_read_dir,
)
from app.services.search_service import fold

TreeEntryKind = Literal["folder", "note"]


class TreeEntry(NamedTuple):
    kind: TreeEntryKind
    name: str
    relative: str
    modified_at: datetime | None


class TreePage(NamedTuple):
    folder: str
    entries: list[TreeEntry]
    has_more: bool


def resolve_folder(read_root: Path, folder: str | None) -> ResolvedNote:
    """Resolve ``folder`` (or the vault root) for a tree listing.

    Exposed separately from :func:`list_tree` so a caller can build a cursor's
    fingerprint from the *resolved* relative path before listing — that is
    what makes ``folder="Knowledge"`` and ``folder="Knowledge/"`` share a
    cursor, and resolves the folder exactly once per request either way.
    """
    return resolve_read_dir(folder, read_root)


def list_tree(
    *,
    resolved: ResolvedNote,
    read_root: Path,
    limit: int,
    offset: int,
    timezone: ZoneInfo,
) -> TreePage:
    """List the direct children of an already-resolved folder.

    Folders sort before notes; within each group, entries sort by name. Since
    a directory cannot contain two entries of the same name, this is a strict
    total order — the same one a ``next_cursor`` offset slices into.
    """
    children = [
        TreeEntry(
            kind="folder" if entry.is_dir else "note",
            name=entry.name,
            relative=entry.relative,
            modified_at=(
                None
                if entry.stat_result is None
                else datetime.fromtimestamp(entry.stat_result.st_mtime, tz=timezone)
            ),
        )
        for entry in iter_directory(resolved.path, read_root)
    ]
    children.sort(key=lambda entry: (entry.kind != "folder", entry.name))

    window = children[offset : offset + limit]
    has_more = len(children) > offset + len(window)
    return TreePage(folder=resolved.relative, entries=window, has_more=has_more)


class NameCount(NamedTuple):
    name: str
    count: int


class VaultStats(NamedTuple):
    note_count: int
    total_bytes: int
    folder_count: int
    top_level_folders: list[NameCount]
    tags: list[NameCount]
    last_modified_at: datetime | None
    skipped_count: int


def summarise_vault(
    *,
    read_root: Path,
    top_tags_limit: int,
    timezone: ZoneInfo,
) -> VaultStats:
    """Aggregate counts and sizes over the whole vault without exposing any
    note body, title, or absolute path (PHASE2_PLAN section 4).

    ``folder_count`` counts distinct folders that *directly* contain at least
    one note — not every ancestor directory, and not the vault root itself
    for notes that sit there with no folder at all. ``top_level_folders``
    likewise only counts a note if it sits under some top-level folder; a
    note directly at the vault root still counts toward ``note_count`` and
    ``total_bytes`` but contributes to neither.

    Only frontmatter is ever read — never a note's body — via
    :func:`~app.services.markdown_parser.read_frontmatter_text`, since tags
    are the only thing this needs from a note's content. ``total_bytes``
    comes from :func:`~app.services.path_security.iter_vault_notes`'s own
    ``stat()`` regardless, so it was never affected by how much of the file
    gets read.

    A note that cannot be read after :func:`iter_vault_notes` already stat'd
    it (e.g. a permission or unlink race) is skipped and counted in
    ``skipped_count`` alongside the walk's own skips, rather than raising —
    matching the read path's general tolerance for a vault that changes out
    from under a scan. A note that reads fine but simply has no (or no
    usable) frontmatter is not a failure at all: it is counted normally and
    contributes no tags, exactly as before.
    """
    walk_stats = WalkStats()
    note_count = 0
    total_bytes = 0
    folders_with_notes: set[str] = set()
    top_level_counts: Counter[str] = Counter()
    tag_counts: Counter[str] = Counter()
    tag_labels: dict[str, str] = {}
    last_modified_at: datetime | None = None
    read_failures = 0

    for note in iter_vault_notes(read_root, stats=walk_stats):
        try:
            frontmatter_block = markdown_parser.read_frontmatter_text(note.path)
        except OSError:
            read_failures += 1
            continue

        note_count += 1
        total_bytes += note.stat_result.st_size

        parent = note.relative.rpartition("/")[0]
        if parent:
            folders_with_notes.add(parent)
            top_level_counts[parent.split("/", 1)[0]] += 1

        if frontmatter_block is not None:
            for tag in markdown_parser.parse_frontmatter_tags(frontmatter_block):
                folded = fold(tag)
                tag_counts[folded] += 1
                tag_labels.setdefault(folded, tag)

        modified_at = datetime.fromtimestamp(note.stat_result.st_mtime, tz=timezone)
        if last_modified_at is None or modified_at > last_modified_at:
            last_modified_at = modified_at

    top_level_folders = [
        NameCount(name=name, count=count)
        for name, count in sorted(top_level_counts.items(), key=lambda item: (-item[1], item[0]))
    ]
    tags = [
        NameCount(name=tag_labels[folded], count=count)
        for folded, count in sorted(tag_counts.items(), key=lambda item: (-item[1], item[0]))
    ][:top_tags_limit]

    return VaultStats(
        note_count=note_count,
        total_bytes=total_bytes,
        folder_count=len(folders_with_notes),
        top_level_folders=top_level_folders,
        tags=tags,
        last_modified_at=last_modified_at,
        skipped_count=walk_stats.skipped + read_failures,
    )
