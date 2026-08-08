"""app/services/duplicate_notes.py — scoped duplicate-note detection before
structured chat export (GitHub issue #14; docs/adr/0007-*.md).

Builds a minimal disposable vault directly under ``tmp_path`` (never the
shared fixture vault) with just an ``00_Inbox/ChatGPT`` directory — this
module never looks anywhere else, so nothing outside that directory needs to
exist for these tests.
"""

from __future__ import annotations

import os
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app.exceptions import InternalError
from app.services import path_security
from app.services.chat_export import one_line
from app.services.duplicate_notes import (
    exact_title_key,
    find_duplicate_candidates,
    normalise_keywords,
    normalized_title_key,
    project_key,
)

TZ = ZoneInfo("Asia/Tokyo")
INBOX_RELATIVE_PATH = "00_Inbox/ChatGPT"


@pytest.fixture
def inbox_vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    (root / "00_Inbox" / "ChatGPT").mkdir(parents=True)
    return root.resolve()


@pytest.fixture
def inbox(inbox_vault: Path) -> Path:
    return inbox_vault / "00_Inbox" / "ChatGPT"


def _find(read_root: Path, *, title: str, **kwargs: object):
    kwargs.setdefault("project", None)
    kwargs.setdefault("keywords", None)
    kwargs.setdefault("limit", 5)
    kwargs.setdefault("timezone", TZ)
    return find_duplicate_candidates(
        read_root=read_root, inbox_relative_path=INBOX_RELATIVE_PATH, title=title, **kwargs
    )


def _write_note(
    inbox: Path,
    name: str,
    *,
    title: str | None = None,
    project: str | None = None,
    tags: list[str] | None = None,
    body: str = "Body.\n",
) -> None:
    lines = ["---"]
    if title is not None:
        lines.append(f"title: {title}")
    if project is not None:
        lines.append(f"project: {project}")
    if tags is not None:
        lines.append(f"tags: [{', '.join(tags)}]")
    lines.append("---")
    (inbox / name).write_text("\n".join(lines) + f"\n\n{body}", encoding="utf-8")


# --- Pure key functions --------------------------------------------------------


def test_exact_title_key_matches_chat_export_one_line() -> None:
    for value in ("RTX   5070", "  RTX 5070  ", "ＲＴＸ 5070", "a\nb"):
        assert exact_title_key(value) == one_line(value)


def test_exact_title_key_is_case_and_width_sensitive() -> None:
    assert exact_title_key("RTX 5070") != exact_title_key("rtx 5070")
    assert exact_title_key("RTX") != exact_title_key("ＲＴＸ")


def test_normalized_title_key_folds_case_and_width_and_collapses_whitespace() -> None:
    assert normalized_title_key("ＲＴＸ　5070") == normalized_title_key("rtx   5070")


def test_normalized_title_key_does_not_strip_punctuation() -> None:
    assert normalized_title_key("C++") != normalized_title_key("C")
    assert normalized_title_key("Node.js") != normalized_title_key("Nodejs")


def test_project_key_folds_and_collapses_and_blank_becomes_none() -> None:
    assert project_key("  Gateway   Rewrite  ") == project_key("gateway rewrite")
    assert project_key("") is None
    assert project_key("   ") is None
    assert project_key(None) is None


def test_normalise_keywords_dedupes_by_fold_and_keeps_original_casing() -> None:
    assert normalise_keywords(["ChatGPT", "chatgpt", "  ", "MCP"]) == ["ChatGPT", "MCP"]
    assert normalise_keywords(None) == []


# --- Exact vs. normalized title matching ---------------------------------------


def test_exact_frontmatter_title_match_is_high_confidence(inbox: Path, inbox_vault: Path) -> None:
    _write_note(inbox, "Existing.md", title="RTX 5070 Build")
    result = _find(inbox_vault, title="RTX   5070 Build")
    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    assert candidate.confidence == "high"
    assert candidate.matched_signals == ("exact_title",)
    assert result.recommendation == "confirm"


def test_normalized_only_title_match_without_project_is_medium(
    inbox: Path, inbox_vault: Path
) -> None:
    _write_note(inbox, "Existing.md", title="RTX 5070 Build")
    result = _find(inbox_vault, title="rtx　5070 build")
    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    assert candidate.confidence == "medium"
    assert candidate.matched_signals == ("normalized_title",)
    assert result.recommendation == "choose"  # medium-only is never "confirm"


def test_normalized_title_plus_project_match_is_high(inbox: Path, inbox_vault: Path) -> None:
    _write_note(inbox, "Existing.md", title="RTX 5070 Build", project="Gateway")
    result = _find(inbox_vault, title="rtx 5070 build", project="gateway")
    candidate = result.candidates[0]
    assert candidate.confidence == "high"
    assert set(candidate.matched_signals) == {"normalized_title", "project"}
    assert result.recommendation == "confirm"


def test_exact_title_signal_excludes_normalized_title_signal(
    inbox: Path, inbox_vault: Path
) -> None:
    _write_note(inbox, "Existing.md", title="RTX 5070 Build")
    result = _find(inbox_vault, title="RTX 5070 Build")
    assert result.candidates[0].matched_signals == ("exact_title",)
    assert "normalized_title" not in result.candidates[0].matched_signals


def test_exact_title_only_applies_to_frontmatter_titles(inbox: Path, inbox_vault: Path) -> None:
    # No frontmatter `title` at all: the file-name stem is the fallback
    # display title, and a stem match can only ever reach normalized_title.
    _write_note(inbox, "RTX 5070 Build.md")
    result = _find(inbox_vault, title="RTX 5070 Build")
    candidate = result.candidates[0]
    assert candidate.confidence == "medium"
    assert candidate.matched_signals == ("normalized_title",)


# --- Sequence-suffix handling (filename fallback only) --------------------------


def test_sequence_suffix_stripped_only_for_filename_fallback(
    inbox: Path, inbox_vault: Path
) -> None:
    # No frontmatter title -> falls back to the file stem "Issue-2", and the
    # suffix-stripped variant "Issue" is also compared.
    _write_note(inbox, "Issue-2.md")
    result = _find(inbox_vault, title="Issue")
    assert len(result.candidates) == 1
    assert result.candidates[0].confidence == "medium"


def test_real_frontmatter_title_with_dash_number_is_never_rounded(
    inbox: Path, inbox_vault: Path
) -> None:
    # A genuine frontmatter title "Issue-2" must never be treated as if it
    # were the de-duplication suffix of "Issue".
    _write_note(inbox, "Issue-2.md", title="Issue-2")
    result = _find(inbox_vault, title="Issue")
    assert result.candidates == ()
    assert result.recommendation == "create"


# --- Project matching -----------------------------------------------------------


def test_project_none_on_both_sides_is_not_a_match(inbox: Path, inbox_vault: Path) -> None:
    _write_note(inbox, "Existing.md", title="RTX 5070 Build")
    result = _find(inbox_vault, title="rtx 5070 build", project=None)
    # Would be "high" if None==None counted as a project match.
    assert result.candidates[0].confidence == "medium"
    assert "project" not in result.candidates[0].matched_signals


def test_project_match_alone_is_never_reported(inbox: Path, inbox_vault: Path) -> None:
    _write_note(inbox, "Existing.md", title="Something Unrelated", project="Gateway")
    result = _find(inbox_vault, title="Totally Different Title", project="gateway")
    assert result.candidates == ()
    assert result.recommendation == "create"


def test_project_match_with_two_keywords_is_medium(inbox: Path, inbox_vault: Path) -> None:
    _write_note(
        inbox, "Existing.md", title="Unrelated Title", project="Gateway", tags=["alpha", "beta"]
    )
    result = _find(
        inbox_vault,
        title="Different Title",
        project="gateway",
        keywords=["alpha", "beta", "gamma"],
    )
    candidate = result.candidates[0]
    assert candidate.confidence == "medium"
    assert set(candidate.matched_signals) == {"project", "keywords"}


# --- Keyword-only matching --------------------------------------------------------


def test_keywords_alone_below_threshold_is_not_reported(inbox: Path, inbox_vault: Path) -> None:
    _write_note(inbox, "Existing.md", title="Unrelated", tags=["alpha"])
    # 3 keywords total, threshold = max(2, ceil(3/2)) = 2; only 1 matches.
    result = _find(inbox_vault, title="Something else", keywords=["alpha", "beta", "gamma"])
    assert result.candidates == ()


def test_keywords_alone_meeting_threshold_is_low(inbox: Path, inbox_vault: Path) -> None:
    _write_note(inbox, "Existing.md", title="Unrelated", tags=["alpha", "beta"])
    result = _find(inbox_vault, title="Something else", keywords=["alpha", "beta", "gamma"])
    candidate = result.candidates[0]
    assert candidate.confidence == "low"
    assert candidate.matched_signals == ("keywords",)


def test_low_only_candidates_recommend_create_not_confirm(
    inbox: Path, inbox_vault: Path
) -> None:
    _write_note(inbox, "Existing.md", title="Unrelated", tags=["alpha", "beta"])
    result = _find(inbox_vault, title="Something else", keywords=["alpha", "beta"])
    assert result.candidates[0].confidence == "low"
    assert result.recommendation == "create"  # never confirm/choose on low alone


def test_keywords_match_title_and_tags_but_never_body(inbox: Path, inbox_vault: Path) -> None:
    _write_note(
        inbox,
        "Existing.md",
        title="Contains alpha in title",
        tags=["beta"],
        body="gamma appears only here, in the body.\n",
    )
    result = _find(inbox_vault, title="Different", keywords=["alpha", "beta", "gamma"])
    assert set(result.candidates[0].matched_keywords) == {"alpha", "beta"}


# --- recommendation combinations -------------------------------------------------


def test_single_high_candidate_recommends_confirm(inbox: Path, inbox_vault: Path) -> None:
    _write_note(inbox, "Existing.md", title="Shared Title")
    result = _find(inbox_vault, title="Shared Title")
    assert result.recommendation == "confirm"


def test_two_high_candidates_recommend_choose(inbox: Path, inbox_vault: Path) -> None:
    _write_note(inbox, "First.md", title="Shared Title")
    _write_note(inbox, "Second.md", title="Shared Title")
    result = _find(inbox_vault, title="Shared Title")
    assert result.recommendation == "choose"


def test_high_plus_medium_recommends_choose(inbox: Path, inbox_vault: Path) -> None:
    _write_note(inbox, "Exact.md", title="Shared Title")
    _write_note(inbox, "Loose.md", title="shared　title")
    result = _find(inbox_vault, title="Shared Title")
    assert result.recommendation == "choose"


def test_medium_only_recommends_choose(inbox: Path, inbox_vault: Path) -> None:
    _write_note(inbox, "Loose.md", title="shared　title")
    result = _find(inbox_vault, title="Shared Title")
    assert result.recommendation == "choose"


def test_no_candidates_recommends_create(inbox: Path, inbox_vault: Path) -> None:
    _write_note(inbox, "Existing.md", title="Completely Unrelated")
    result = _find(inbox_vault, title="Nothing In Common Whatsoever")
    assert result.candidates == ()
    assert result.candidate_count == 0
    assert result.recommendation == "create"


# --- recommendation/candidate_count computed before slicing ---------------------


def test_recommendation_is_decided_before_limit_slices_the_list(
    inbox: Path, inbox_vault: Path
) -> None:
    _write_note(inbox, "First.md", title="Shared Title")
    _write_note(inbox, "Second.md", title="Shared Title")
    result = _find(inbox_vault, title="Shared Title", limit=1)
    assert len(result.candidates) == 1
    assert result.candidate_count == 2  # truncated = candidate_count > len(candidates)
    assert result.recommendation == "choose"  # not "confirm", despite only 1 candidate returned


def test_limit_rejects_zero(inbox: Path, inbox_vault: Path) -> None:
    with pytest.raises(ValueError, match="limit"):
        _find(inbox_vault, title="x", limit=0)


def test_ordering_is_deterministic_by_score_then_mtime_then_path(
    inbox: Path, inbox_vault: Path
) -> None:
    _write_note(inbox, "B.md", title="Shared Title")
    _write_note(inbox, "A.md", title="Shared Title")
    result = _find(inbox_vault, title="Shared Title", limit=10)
    # Equal score/mtime (created back-to-back) -> tie-break alphabetically.
    paths = [c.relative for c in result.candidates]
    assert paths == sorted(paths)


def test_candidates_are_ordered_by_confidence_before_raw_score(
    inbox: Path, inbox_vault: Path
) -> None:
    """A bug this pins: `score` alone is not monotonic in confidence — a
    keyword-only "low" candidate with enough matches can out-score a
    "medium" one (a project match plus fewer keywords). `candidates`'
    documented contract ("most confident first") requires confidence to be
    the primary sort key, so a small `limit` never keeps the less-confident,
    merely-higher-scoring candidate over the more-confident one.
    """
    ten_keywords = [f"k{i}" for i in range(1, 11)]

    # medium: project match + exactly 2 matched keywords -> score 100 + 2*20 = 140
    _write_note(
        inbox, "Medium.md", title="Something Else A", project="Alpha", tags=ten_keywords[:2]
    )
    # low: keywords only (no project), 8/10 matched (threshold is 5) -> score 8*20 = 160
    _write_note(inbox, "Low.md", title="Something Else B", tags=ten_keywords[:8])

    result = _find(
        inbox_vault, title="Search Title", project="Alpha", keywords=ten_keywords, limit=10
    )
    assert [c.confidence for c in result.candidates] == ["medium", "low"]

    # The higher-scoring "low" candidate must not push the "medium" one out
    # when limit=1 slices the (pre-computed) full set.
    truncated = _find(
        inbox_vault, title="Search Title", project="Alpha", keywords=ten_keywords, limit=1
    )
    assert len(truncated.candidates) == 1
    assert truncated.candidates[0].confidence == "medium"
    assert truncated.candidate_count == 2


# --- Scope: inbox-only, shallow, frontmatter-only --------------------------------


def test_scan_is_shallow_and_excludes_non_candidates(inbox: Path, inbox_vault: Path) -> None:
    _write_note(inbox, "Match.md", title="Target Title")

    sub = inbox / "Sub"
    sub.mkdir()
    _write_note(sub, "Nested.md", title="Target Title")

    _write_note(inbox, ".hidden.md", title="Target Title")
    (inbox / "not-markdown.txt").write_text("title: Target Title\n", encoding="utf-8")

    _write_note(
        inbox,
        "BodyOnly.md",
        title="Something else",
        body="Target Title is mentioned only here, in the body.\n",
    )

    outside = inbox_vault / "Outside.md"
    outside.write_text("---\ntitle: Target Title\n---\n", encoding="utf-8")
    (inbox / "Link.md").symlink_to(outside)

    result = _find(inbox_vault, title="Target Title")
    assert {c.relative for c in result.candidates} == {"00_Inbox/ChatGPT/Match.md"}
    # Match.md + BodyOnly.md were read; the rest were never even opened.
    assert result.scanned_count == 2
    assert result.skipped_count == 0


# --- Degradation on malformed input ---------------------------------------------


def test_malformed_and_missing_frontmatter_degrades_without_raising(
    inbox: Path, inbox_vault: Path
) -> None:
    (inbox / "BadYaml.md").write_text("---\ntitle: [unterminated\n---\n", encoding="utf-8")
    (inbox / "NoFrontmatter.md").write_text("# Just a heading\n\nBody\n", encoding="utf-8")
    (inbox / "EmptyTitle.md").write_text('---\ntitle: ""\n---\n', encoding="utf-8")

    result = _find(inbox_vault, title="Anything")
    assert result.scanned_count == 3
    assert result.skipped_count == 0
    assert result.candidates == ()


# --- Scan failure vs. no-match ---------------------------------------------------


def test_directory_scan_failure_raises_internal_error(
    inbox: Path, inbox_vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_note(inbox, "Foo.md", title="Foo")

    def _raise(*_args: object, **_kwargs: object):
        raise PermissionError("denied")

    monkeypatch.setattr(path_security.os, "scandir", _raise)
    with pytest.raises(InternalError):
        _find(inbox_vault, title="Foo")


@pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="root bypasses directory permission bits, so chmod 000 would not reproduce a failure",
)
def test_directory_scan_failure_via_permission_bits(inbox: Path, inbox_vault: Path) -> None:
    _write_note(inbox, "Foo.md", title="Foo")
    inbox.chmod(0o000)
    try:
        with pytest.raises(InternalError):
            _find(inbox_vault, title="Foo")
    finally:
        inbox.chmod(0o755)


# --- Append-path-syntax validation ----------------------------------------------


def test_backslash_named_file_is_excluded_and_counted_as_skipped(
    inbox: Path, inbox_vault: Path
) -> None:
    # Valid on Linux, but append_inbox_note's own path_security check would
    # reject it (backslash in path) — so it must never be offered as a
    # candidate append target in the first place.
    bad_name = "weird\\name.md"
    (inbox / bad_name).write_text("---\ntitle: Weird Backslash Note\n---\n", encoding="utf-8")

    result = _find(inbox_vault, title="Weird Backslash Note")
    assert result.candidates == ()
    assert result.scanned_count == 0
    assert result.skipped_count == 1


def test_every_returned_path_passes_append_path_validation(
    inbox: Path, inbox_vault: Path
) -> None:
    _write_note(inbox, "Good Note.md", title="Good Note")
    result = _find(inbox_vault, title="Good Note")
    for candidate in result.candidates:
        path_security.normalise_relative_path(candidate.relative)  # must not raise


# --- Response hygiene: no absolute paths, no body, no internal score ------------


def test_candidate_has_no_score_attribute(inbox: Path, inbox_vault: Path) -> None:
    _write_note(inbox, "Existing.md", title="Shared Title")
    result = _find(inbox_vault, title="Shared Title")
    assert not hasattr(result.candidates[0], "score")


def test_candidate_path_is_vault_relative_not_absolute(inbox: Path, inbox_vault: Path) -> None:
    _write_note(inbox, "Existing.md", title="Shared Title")
    result = _find(inbox_vault, title="Shared Title")
    candidate = result.candidates[0]
    assert not candidate.relative.startswith("/")
    assert str(inbox_vault) not in candidate.relative


def test_candidate_never_carries_note_body_text(inbox: Path, inbox_vault: Path) -> None:
    secret_body = "this-body-text-must-never-appear-in-any-field\n"
    _write_note(inbox, "Existing.md", title="Shared Title", body=secret_body)
    result = _find(inbox_vault, title="Shared Title")
    candidate = result.candidates[0]
    rendered = repr(candidate)
    assert "this-body-text-must-never-appear-in-any-field" not in rendered
