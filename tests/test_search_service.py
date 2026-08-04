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
