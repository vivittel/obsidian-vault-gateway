"""app.services.search_service — the fold(body)-skip optimisation.

NFKC-folding a note's body (search_service.fold) is the single most
expensive step of a search: full-width/CJK text defeats NFKC's ASCII fast
path, so on a Japanese vault this can cost more per note than everything
else in the loop combined. A tags-only or unfiltered search never uses the
result — _build_excerpt short-circuits on an empty folded_query before ever
touching folded_body — so search_notes only computes it when there is a
query to fold against.

These tests prove the skip actually happens (via a spy on fold itself), not
just that the response looks the same — a passing behavioural test alone
would not catch a regression that folds the body and then simply discards
it.
"""

from __future__ import annotations

from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app.services import search_service

UTC = ZoneInfo("UTC")
UNIQUE_BODY_MARKER = "UNIQUE_BODY_MARKER_9f3a"


@pytest.fixture
def tagged_vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "note.md").write_text(
        f"---\ntags: [nvidia]\n---\n\n{UNIQUE_BODY_MARKER} and more body text.\n",
        encoding="utf-8",
    )
    return vault


def _spy_on_fold(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    original_fold = search_service.fold
    calls: list[str] = []

    def spy(text: str) -> str:
        calls.append(text)
        return original_fold(text)

    monkeypatch.setattr(search_service, "fold", spy)
    return calls


def _folded_the_body(calls: list[str]) -> bool:
    return any(UNIQUE_BODY_MARKER in call for call in calls)


def test_tag_only_search_never_folds_the_note_body(
    tagged_vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _spy_on_fold(monkeypatch)
    page = search_service.search_notes(
        read_root=tagged_vault, tags="nvidia", timezone=UTC, max_note_bytes=1_048_576
    )
    assert len(page.hits) == 1
    assert not _folded_the_body(calls)


def test_query_less_search_never_folds_the_note_body(
    tagged_vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _spy_on_fold(monkeypatch)
    page = search_service.search_notes(
        read_root=tagged_vault, timezone=UTC, max_note_bytes=1_048_576
    )
    assert len(page.hits) == 1
    assert not _folded_the_body(calls)


def test_empty_string_query_never_folds_the_note_body(
    tagged_vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _spy_on_fold(monkeypatch)
    search_service.search_notes(
        read_root=tagged_vault, query="", timezone=UTC, max_note_bytes=1_048_576
    )
    assert not _folded_the_body(calls)


def test_whitespace_only_query_never_folds_the_note_body(
    tagged_vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _spy_on_fold(monkeypatch)
    search_service.search_notes(
        read_root=tagged_vault, query="   ", timezone=UTC, max_note_bytes=1_048_576
    )
    assert not _folded_the_body(calls)


def test_a_real_query_still_folds_the_note_body(
    tagged_vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The skip must not swallow the query-present case — a query needs the
    # folded body for both scoring and the excerpt.
    calls = _spy_on_fold(monkeypatch)
    page = search_service.search_notes(
        read_root=tagged_vault, query=UNIQUE_BODY_MARKER, timezone=UTC, max_note_bytes=1_048_576
    )
    assert len(page.hits) == 1
    assert _folded_the_body(calls)


def test_tag_only_search_excerpt_is_still_the_note_head(tagged_vault: Path) -> None:
    # _build_excerpt's no-query fallback (head of body) must still work
    # correctly now that folded_body is "" rather than a real fold in this
    # branch — confirms the empty placeholder never leaks into the excerpt.
    page = search_service.search_notes(
        read_root=tagged_vault, tags="nvidia", timezone=UTC, max_note_bytes=1_048_576
    )
    assert page.hits[0].excerpt.startswith(UNIQUE_BODY_MARKER)


@pytest.mark.parametrize(
    "invisible_character",
    ["​", "﻿", "\xad", "‌", "‍"],
    ids=["zero-width-space", "bom", "soft-hyphen", "zwnj", "zwj"],
)
def test_stripped_nonempty_query_never_folds_to_an_empty_string(invisible_character: str) -> None:
    # The skip is gated on `folded_query` truthiness, the same condition
    # _score already used — safe only because fold() never turns a
    # str.strip()-nonempty string into "". Confirmed here across the
    # invisible/zero-width characters most likely to be a counter-example
    # (none of them are stripped by str.strip(), and none fold to empty).
    assert invisible_character.strip() != ""
    assert search_service.fold(invisible_character) != ""


# --- a note that cannot be read mid-scan (bug: used to raise OSError past the
# whole search, matching vault_service.summarise_vault's tolerance for the
# same race) ------------------------------------------------------------------


@pytest.fixture
def two_note_vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "readable.md").write_text(f"{UNIQUE_BODY_MARKER} readable.\n", encoding="utf-8")
    (vault / "unreadable.md").write_text("this one will fail to open.\n", encoding="utf-8")
    return vault


def test_a_note_that_fails_to_open_is_skipped_not_fatal(
    two_note_vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_read_note_text = search_service.markdown_parser.read_note_text

    def flaky_read_note_text(path: Path, **kwargs: object):
        if path.name == "unreadable.md":
            raise OSError("simulated permission or unlink race")
        return original_read_note_text(path, **kwargs)

    monkeypatch.setattr(search_service.markdown_parser, "read_note_text", flaky_read_note_text)

    page = search_service.search_notes(
        read_root=two_note_vault, timezone=UTC, max_note_bytes=1_048_576
    )

    assert [hit.relative for hit in page.hits] == ["readable.md"]
    assert page.skipped_count == 1


def test_a_matching_query_still_skips_an_unreadable_note(
    two_note_vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_read_note_text = search_service.markdown_parser.read_note_text

    def flaky_read_note_text(path: Path, **kwargs: object):
        if path.name == "unreadable.md":
            raise OSError("simulated permission or unlink race")
        return original_read_note_text(path, **kwargs)

    monkeypatch.setattr(search_service.markdown_parser, "read_note_text", flaky_read_note_text)

    page = search_service.search_notes(
        read_root=two_note_vault, query=UNIQUE_BODY_MARKER, timezone=UTC, max_note_bytes=1_048_576
    )

    assert [hit.relative for hit in page.hits] == ["readable.md"]
    assert page.skipped_count == 1


def test_no_unreadable_notes_means_skipped_count_is_zero(two_note_vault: Path) -> None:
    page = search_service.search_notes(
        read_root=two_note_vault, timezone=UTC, max_note_bytes=1_048_576
    )
    assert page.skipped_count == 0


def test_folder_scoped_search_does_not_count_failures_outside_the_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bug this pins: SearchResponse.skipped_count is documented as
    "notes that matched the requested scope but could not be read" — a
    folder-scoped search must not count a stat() failure that happened
    outside the requested folder, which the original implementation did
    (iter_vault_notes stat'd, and counted, every file in the vault before
    search_notes's own folder filter ever ran).
    """
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "Knowledge").mkdir()
    (vault / "Knowledge" / "Good.md").write_text("readable.\n", encoding="utf-8")
    (vault / "Private").mkdir()
    (vault / "Private" / "Bad.md").write_text("also on disk.\n", encoding="utf-8")

    import os as os_module

    original_os_stat = os_module.stat

    def flaky_os_stat(path: object, *, dir_fd: object = None, follow_symlinks: bool = True):
        # iter_vault_notes checks is_symlink() (follow_symlinks=False, via
        # lstat) before its own explicit stat() call (follow_symlinks=True,
        # the default). Only failing the latter targets that specific call
        # without also breaking the symlink check every entry goes through
        # (see tests/test_vault.py's identical pattern).
        name = getattr(path, "name", None) or os_module.path.basename(os_module.fspath(path))
        if follow_symlinks and name == "Bad.md":
            raise OSError("simulated permission or unlink race")
        return original_os_stat(path, dir_fd=dir_fd, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(os_module, "stat", flaky_os_stat)

    in_scope = search_service.search_notes(
        read_root=vault, folder="Knowledge", timezone=UTC, max_note_bytes=1_048_576
    )
    assert [hit.relative for hit in in_scope.hits] == ["Knowledge/Good.md"]
    assert in_scope.skipped_count == 0

    out_of_scope = search_service.search_notes(
        read_root=vault, folder="Private", timezone=UTC, max_note_bytes=1_048_576
    )
    assert out_of_scope.hits == []
    assert out_of_scope.skipped_count == 1


# --- excerpt around a query with leading/trailing whitespace (bug: the raw,
# unstripped query was passed to _build_excerpt while folded_query used the
# stripped form, so a length-changing fold — full-width/CJK — fell through
# to the "no match" head-of-note fallback even though the body matched) -----


def test_excerpt_finds_a_length_changing_match_despite_surrounding_whitespace(
    tmp_path: Path,
) -> None:
    # A long preamble (over EXCERPT_HEAD=200 chars) so the head-of-note
    # fallback and a real match produce visibly different excerpts. "ß"
    # casefolds to "ss" — a length change that forces _build_excerpt's
    # re-find-the-literal-query branch, exactly like the length changes NFKC
    # composition/decomposition produce on a real vault. Confirmed to
    # reproduce the bug directly: with the raw (unstripped) query passed to
    # that branch instead of the stripped one, `re.escape("  Straße  ")`
    # never matches the single-spaced "Straße" in the body, and this test
    # falls back to the head of the note instead of the real match.
    vault = tmp_path / "vault"
    vault.mkdir()
    preamble = "Filler sentence to push well past the two-hundred character head. " * 3
    body = f"{preamble}\n\nStraße details follow here.\n"
    (vault / "note.md").write_text(body, encoding="utf-8")

    page = search_service.search_notes(
        read_root=vault, query="  Straße  ", timezone=UTC, max_note_bytes=1_048_576
    )

    assert len(page.hits) == 1
    assert "details follow" in page.hits[0].excerpt
