"""Full-vault search (IMPLEMENTATION_PLAN section 6.2).

Phase 1 scans the vault on every query: a linear walk, read, parse and substring
match. That fits the stated target (section 17: usually under 3 s) for a vault of
a few thousand notes, and keeps the whole search path auditable. Section 4 of the
plan is where the SQLite FTS5 migration lives once the vault outgrows this.

Matching normalises both sides with NFKC + casefold. NFKC is what makes Japanese
search behave: it folds full-width to half-width (ＲＴＸ → RTX, ５０７０ → 5070)
and composes decomposed kana, which vaults synced from macOS contain.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import NamedTuple
from zoneinfo import ZoneInfo

from app.services import markdown_parser
from app.services.path_security import iter_vault_notes, normalise_relative_dir

DEFAULT_LIMIT = 20
EXCERPT_RADIUS = 100
EXCERPT_HEAD = 200

# Field weights. Section 6.2 lists the searchable fields; ordering them means
# ChatGPT sees the note whose *title* matches before one that mentions the term
# in passing.
_SCORE_TITLE = 400
_SCORE_FILENAME = 300
_SCORE_HEADING = 200
_SCORE_TAG = 100
_SCORE_BODY = 10


class SearchHit(NamedTuple):
    relative: str
    title: str
    excerpt: str
    tags: list[str]
    modified_at: datetime
    score: int


def fold(text: str) -> str:
    """NFKC + casefold, the single normalisation used on both sides of a match."""
    return unicodedata.normalize("NFKC", text).casefold()


def parse_tag_filter(raw: str | None) -> list[str]:
    """Split a comma-separated ``tags`` query value into folded tag names."""
    if not raw:
        return []
    return [fold(part.strip()) for part in raw.split(",") if part.strip()]


def _collapse_whitespace(text: str) -> str:
    return " ".join(text.split())


def _build_excerpt(body: str, folded_body: str, query: str, folded_query: str) -> str:
    """A short window around the first body match, else the head of the note."""
    index = folded_body.find(folded_query) if folded_query else -1

    if index >= 0 and len(folded_body) != len(body):
        # Folding changed the length (compatibility characters, decomposed marks),
        # so the folded offset does not address the original text. Re-find the
        # literal query; if that fails, fall back to the head of the note.
        match = re.search(re.escape(query), body, re.IGNORECASE)
        index = match.start() if match else -1

    if index < 0:
        return _collapse_whitespace(body[:EXCERPT_HEAD])

    start = max(0, index - EXCERPT_RADIUS)
    return _collapse_whitespace(body[start : index + len(query) + EXCERPT_RADIUS])


def _score(
    folded_query: str,
    *,
    folded_title: str,
    folded_stem: str,
    folded_tags: Sequence[str],
    folded_headings: Sequence[str],
    folded_body: str,
) -> int:
    score = 0
    if folded_query in folded_title:
        score += _SCORE_TITLE
    if folded_query in folded_stem:
        score += _SCORE_FILENAME
    if any(folded_query in heading for heading in folded_headings):
        score += _SCORE_HEADING
    if any(folded_query in tag for tag in folded_tags):
        score += _SCORE_TAG
    if folded_query in folded_body:
        score += _SCORE_BODY
    return score


def search_notes(
    *,
    read_root: Path,
    query: str | None = None,
    folder: str | None = None,
    tags: str | None = None,
    limit: int = DEFAULT_LIMIT,
    timezone: ZoneInfo,
    max_note_bytes: int,
) -> list[SearchHit]:
    """Search the vault.

    ``query`` matches file name, frontmatter title, frontmatter tags, headings and
    body. ``tags`` narrows by frontmatter tags and is an **AND**: every tag listed
    must be present. ``folder`` restricts to a subtree. With no ``query`` the
    filters alone select notes, newest first.
    """
    folded_query = fold(query.strip()) if query and query.strip() else ""
    required_tags = parse_tag_filter(tags)
    prefix = f"{normalise_relative_dir(folder).as_posix()}/" if folder else ""

    hits: list[SearchHit] = []
    for note in iter_vault_notes(read_root):
        if prefix and not note.relative.startswith(prefix):
            continue

        text, _ = markdown_parser.read_note_text(
            note.path,
            size_bytes=note.stat_result.st_size,
            max_bytes=max_note_bytes,
        )
        stem = note.relative.rsplit("/", 1)[-1].removesuffix(".md")
        parsed = markdown_parser.parse_note(text, fallback_title=stem)

        folded_tags = [fold(tag) for tag in parsed.tags]
        if required_tags and not all(required in folded_tags for required in required_tags):
            continue

        folded_body = fold(parsed.body)
        score = 0
        if folded_query:
            score = _score(
                folded_query,
                folded_title=fold(parsed.title),
                folded_stem=fold(stem),
                folded_tags=folded_tags,
                folded_headings=[fold(heading) for heading in parsed.headings],
                folded_body=folded_body,
            )
            if score == 0:
                continue

        hits.append(
            SearchHit(
                relative=note.relative,
                title=parsed.title,
                excerpt=_build_excerpt(parsed.body, folded_body, query or "", folded_query),
                tags=parsed.tags,
                modified_at=datetime.fromtimestamp(note.stat_result.st_mtime, tz=timezone),
                score=score,
            )
        )

    hits.sort(key=lambda hit: (-hit.score, -hit.modified_at.timestamp(), hit.relative))
    return hits[:limit]
