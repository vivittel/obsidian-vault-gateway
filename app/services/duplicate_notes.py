"""Scoped duplicate-note detection before structured chat export (GitHub
issue #14; docs/adr/0007-scoped-duplicate-note-detection.md).

Scans only the direct children of ``00_Inbox/ChatGPT`` — never the whole
Vault — and never reads a note's body: only ``read_frontmatter_text``'s
bounded, streaming read of the leading frontmatter block, the same call
:func:`~app.services.vault_service.summarise_vault` already makes. A
candidate's ``title``/``project``/``tags`` come from that block (or a
filename fallback); nothing here ever opens a note past its frontmatter.

This module answers only "what looks similar"; it never decides "may this be
written". The Gateway does not gate ``create_inbox_note``/``append_inbox_note``
on anything computed here (issue #14's safety constraints) — that decision is
a client-workflow contract documented on the MCP tools themselves
(app/mcp_server.py), not an invariant this module enforces.
"""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import NamedTuple
from zoneinfo import ZoneInfo

from app.exceptions import GatewayError, InternalError
from app.services import markdown_parser
from app.services.chat_export import one_line
from app.services.path_security import (
    WalkStats,
    iter_directory,
    normalise_relative_path,
    resolve_read_dir,
)
from app.services.search_service import fold

# Mirrors search_service.py's own weighting style (_SCORE_*): ordering these
# tells a candidate whose *title* matches apart from one that only shares a
# keyword. Never serialised — see DuplicateCandidate's docstring.
_SCORE_EXACT_TITLE = 400
_SCORE_NORMALIZED_TITLE = 300
_SCORE_PROJECT = 100
_SCORE_KEYWORD = 20

# The fixed threshold used when a candidate's *only* signal is keywords (no
# title or project match) — deliberately stricter than the flat "2 or more"
# used when keywords combine with an existing project match (see
# _confidence): keywords alone are the weakest signal this module has, so
# they need to be more numerous, not just present, to justify surfacing a
# candidate on their own.
_MIN_KEYWORD_MATCHES_ALONE = 2

# Matches the sequence suffix app.services.filenames.note_file_name appends
# ("-2", "-3", ...) — never anything chat_export writes into a title itself.
_SEQUENCE_SUFFIX_RE = re.compile(r"-\d+$")

# The primary sort key for `candidates` (DuplicateCandidatesResponse.candidates'
# own docstring: "most confident first"). Needed because `score` alone is not
# monotonic in confidence — a keyword-only "low" candidate with many matched
# keywords can out-score a "medium" one with fewer, higher-value signals (a
# project match plus two keywords: 100 + 2*20 = 140 vs. keywords alone at, say,
# 8 matches: 8*20 = 160) — exactly the case that let `limit` truncate away the
# more-confident candidate while keeping a less-confident one ranked above it.
_CONFIDENCE_RANK = {"high": 0, "medium": 1, "low": 2}


def exact_title_key(title: str) -> str:
    """The same canonicalisation a structured export's own title goes
    through when it is written (:func:`app.services.chat_export.one_line`).

    Reused rather than re-implemented: an independent "exact" normalisation
    could silently drift from what actually ends up in a note's frontmatter
    `title`/H1, which would make "exact" stop meaning "exact". No casefold,
    no NFKC — ``"RTX 5070"`` and ``"rtx 5070"`` are deliberately different
    keys here (that gap is what :func:`normalized_title_key` is for).
    """
    return one_line(title)


def normalized_title_key(title: str) -> str:
    """NFKC + casefold (:func:`app.services.search_service.fold`), with
    internal whitespace collapsed to a single space.

    Looser than :func:`exact_title_key`: ``"ＲＴＸ　5070"`` and ``"rtx 5070"``
    share a key here but not there. No punctuation stripping — issue #14
    asks for normalized-title matching, not fuzzy matching, and stripping
    symbols would collapse distinct titles like "C++" and "C" onto the same
    key.
    """
    return " ".join(fold(title).split())


def project_key(project: str | None) -> str | None:
    """NFKC + casefold + whitespace-collapse, or ``None`` for missing/blank.

    ``None`` is a real outcome, not an edge case to paper over: issue #14
    requires "same project metadata *when available*", not "both notes
    happen to have none" — two notes that both omit `project` are not
    thereby a project match (see :func:`find_duplicate_candidates`).
    """
    if project is None:
        return None
    normalised = " ".join(fold(project).split())
    return normalised or None


def normalise_keywords(keywords: Sequence[str] | None) -> list[str]:
    """Strip and dedupe ``keywords``, preserving input order and casing.

    Deduplication is decided by the folded key (so ``"ChatGPT"`` and
    ``"chatgpt"`` collapse to one entry), but the *returned* strings keep
    their original casing/width — this is what a caller echoes back as
    ``matched_keywords``, and folding it for display would be a needless
    second lossy transform.
    """
    seen: set[str] = set()
    result: list[str] = []
    for keyword in keywords or []:
        stripped = keyword.strip()
        if not stripped:
            continue
        folded = fold(stripped)
        if folded in seen:
            continue
        seen.add(folded)
        result.append(stripped)
    return result


def _strip_sequence_suffix(stem: str) -> str:
    return _SEQUENCE_SUFFIX_RE.sub("", stem)


def _confidence(
    *,
    title_signal: str | None,
    project_match: bool,
    matched_keyword_count: int,
    total_keywords: int,
) -> str | None:
    """Decide a candidate's confidence, or ``None`` to drop it entirely.

    Ordered most-specific-first; see docs/adr/0007-*.md for why each branch
    sits where it does — in particular why a project match alone is never
    enough (it would turn "same project" into "every note in that project"),
    and why keywords need a stricter bar alone than combined with a project
    match.
    """
    if title_signal == "exact_title":
        return "high"
    if title_signal == "normalized_title" and project_match:
        return "high"
    if title_signal == "normalized_title":
        return "medium"
    if project_match and matched_keyword_count >= 2:
        return "medium"
    if project_match:
        return None
    if matched_keyword_count and total_keywords:
        threshold = max(_MIN_KEYWORD_MATCHES_ALONE, math.ceil(total_keywords / 2))
        if matched_keyword_count >= threshold:
            return "low"
    return None


@dataclass(frozen=True)
class _ScoredCandidate:
    relative: str
    title: str
    project: str | None
    tags: tuple[str, ...]
    confidence: str
    matched_signals: tuple[str, ...]
    matched_keywords: tuple[str, ...]
    modified_at: datetime
    score: int = field(compare=False)
    mtime: float = field(compare=False)


class DuplicateCandidate(NamedTuple):
    """One existing inbox note that may already cover the same conversation.

    Deliberately has no ``score`` field — the caller (app/application.py)
    maps this straight into ``app.models.DuplicateCandidate``, and the
    internal sort key never needs to survive past this module (docs/adr/
    0007-*.md: exposing it would make Gateway's weighting a de facto API
    contract).
    """

    relative: str
    title: str
    project: str | None
    tags: tuple[str, ...]
    confidence: str
    matched_signals: tuple[str, ...]
    matched_keywords: tuple[str, ...]
    modified_at: datetime


class DuplicateCandidatesResult(NamedTuple):
    candidates: tuple[DuplicateCandidate, ...]
    candidate_count: int
    recommendation: str
    scanned_count: int
    skipped_count: int


def find_duplicate_candidates(
    *,
    read_root: Path,
    inbox_relative_path: str,
    title: str,
    project: str | None,
    keywords: Sequence[str] | None,
    limit: int,
    timezone: ZoneInfo,
) -> DuplicateCandidatesResult:
    """Scan the direct children of the inbox for notes that may duplicate
    ``title``/``project``/``keywords``.

    ``recommendation`` is decided from *every* matching candidate, before
    ``limit`` ever slices the list — deciding it after slicing would let a
    small ``limit`` hide a second high-confidence candidate and understate
    the ambiguity (docs/adr/0007-*.md). ``candidate_count`` reports that
    full, pre-slice total; ``len(candidates) < candidate_count`` is exactly
    when the caller should report ``truncated=True``.

    Raises :class:`~app.exceptions.InternalError` if the inbox directory
    itself could not be scanned (as opposed to being scanned and found to
    contain nothing matching) — see :class:`~app.services.path_security.
    WalkStats`. Individual unreadable notes are not fatal: they are excluded
    and counted in ``skipped_count``, the same degradation
    :func:`~app.services.vault_service.summarise_vault` uses for the whole
    Vault.
    """
    if limit < 1:
        raise ValueError("limit must be at least 1")

    resolved = resolve_read_dir(inbox_relative_path, read_root)

    input_exact = exact_title_key(title)
    input_normalized = normalized_title_key(title)
    input_project = project_key(project)
    input_keywords = normalise_keywords(keywords)
    folded_keywords = [fold(keyword) for keyword in input_keywords]

    stats = WalkStats()
    scanned_count = 0
    skipped_count = 0
    scored: list[_ScoredCandidate] = []

    for entry in iter_directory(resolved.path, read_root, stats=stats):
        if entry.is_dir or entry.stat_result is None:
            continue

        try:
            normalise_relative_path(entry.relative)
        except GatewayError:
            # Syntactically real on disk, but not a name append_inbox_note
            # would ever accept (e.g. a literal backslash) — dropped before
            # it can be offered as an append target it cannot actually be.
            skipped_count += 1
            continue

        note_path = resolved.path / entry.name
        try:
            block = markdown_parser.read_frontmatter_text(note_path)
        except OSError:
            skipped_count += 1
            continue

        scanned_count += 1
        metadata = markdown_parser.parse_frontmatter_metadata(block) if block else {}

        raw_title = metadata.get("title")
        has_frontmatter_title = isinstance(raw_title, str) and bool(raw_title.strip())
        stem = entry.name.removesuffix(".md")
        display_title = raw_title.strip() if has_frontmatter_title else stem

        tags = tuple(markdown_parser.normalise_tags(metadata.get("tags")))
        raw_project = metadata.get("project")
        candidate_project = raw_project if isinstance(raw_project, str) else None
        candidate_project_key = project_key(candidate_project)

        # Title signal is exclusive (exact > normalized > none): an exact
        # match must never also count as a separate normalized-title match
        # for the same title, or the two would double up in both the score
        # and matched_signals (docs/adr/0007-*.md).
        exact_match = has_frontmatter_title and exact_title_key(display_title) == input_exact
        normalized_variants = {normalized_title_key(display_title)}
        if not has_frontmatter_title:
            # A note with no frontmatter title falls back to its file name,
            # which may carry create_inbox_note's own de-duplication suffix
            # ("Foo-2.md"). Only that fallback gets this extra, stripped
            # variant compared — a real frontmatter title is never rewritten
            # (a genuine "Issue-2" must stay "Issue-2", never become "Issue").
            stripped_stem = _strip_sequence_suffix(stem)
            if stripped_stem != stem:
                normalized_variants.add(normalized_title_key(stripped_stem))
        normalized_match = not exact_match and input_normalized in normalized_variants

        if exact_match:
            title_signal: str | None = "exact_title"
        elif normalized_match:
            title_signal = "normalized_title"
        else:
            title_signal = None

        project_match = input_project is not None and candidate_project_key == input_project

        folded_title = fold(display_title)
        folded_tags = [fold(tag) for tag in tags]
        matched_keywords = tuple(
            keyword
            for keyword, folded_keyword in zip(input_keywords, folded_keywords, strict=True)
            if folded_keyword in folded_title or any(folded_keyword in tag for tag in folded_tags)
        )

        confidence = _confidence(
            title_signal=title_signal,
            project_match=project_match,
            matched_keyword_count=len(matched_keywords),
            total_keywords=len(folded_keywords),
        )
        if confidence is None:
            continue

        signals: list[str] = []
        if title_signal:
            signals.append(title_signal)
        if project_match:
            signals.append("project")
        if matched_keywords:
            signals.append("keywords")

        score = 0
        if title_signal == "exact_title":
            score += _SCORE_EXACT_TITLE
        elif title_signal == "normalized_title":
            score += _SCORE_NORMALIZED_TITLE
        if project_match:
            score += _SCORE_PROJECT
        score += len(matched_keywords) * _SCORE_KEYWORD

        mtime = entry.stat_result.st_mtime
        scored.append(
            _ScoredCandidate(
                relative=entry.relative,
                title=display_title,
                project=candidate_project,
                tags=tags,
                confidence=confidence,
                matched_signals=tuple(signals),
                matched_keywords=matched_keywords,
                modified_at=datetime.fromtimestamp(mtime, tz=timezone),
                score=score,
                mtime=mtime,
            )
        )

    if stats.scan_failed:
        raise InternalError(
            "The inbox could not be scanned for duplicates.",
            log_detail="duplicate_notes: iter_directory scan_failed",
        )
    skipped_count += stats.skipped

    scored.sort(
        key=lambda candidate: (
            _CONFIDENCE_RANK[candidate.confidence],
            -candidate.score,
            -candidate.mtime,
            candidate.relative,
        )
    )

    high_count = sum(1 for candidate in scored if candidate.confidence == "high")
    medium_count = sum(1 for candidate in scored if candidate.confidence == "medium")
    if high_count == 0 and medium_count == 0:
        recommendation = "create"
    elif high_count == 1 and medium_count == 0:
        recommendation = "confirm"
    else:
        recommendation = "choose"

    candidates = tuple(
        DuplicateCandidate(
            relative=candidate.relative,
            title=candidate.title,
            project=candidate.project,
            tags=candidate.tags,
            confidence=candidate.confidence,
            matched_signals=candidate.matched_signals,
            matched_keywords=candidate.matched_keywords,
            modified_at=candidate.modified_at,
        )
        for candidate in scored[:limit]
    )

    return DuplicateCandidatesResult(
        candidates=candidates,
        candidate_count=len(scored),
        recommendation=recommendation,
        scanned_count=scanned_count,
        skipped_count=skipped_count,
    )
