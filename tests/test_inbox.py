"""Inbox creation and append tests — drives app.application.GatewayApplication
directly (REST's own `/api/v1/inbox/*` routes were removed; see
docs/adr/0010-*.md). All writes go into the tmp_path vault from conftest —
never a real vault (AGENTS.md).
"""

from __future__ import annotations

import asyncio
import errno
import multiprocessing
import os
import stat
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app.application import GatewayApplication
from app.exceptions import (
    FileTooLargeError,
    InboxLockTimeoutError,
    InvalidFileTypeError,
    InvalidPathError,
    InvalidTitleError,
    NoteModifiedError,
    NoteNotFoundError,
    PathOutsideVaultError,
    ValidationError,
)
from app.services import inbox_service

TOKYO = ZoneInfo("Asia/Tokyo")


def test_create_note_minimal(inbox_root: Path) -> None:
    # inbox_service directly: this raw content/frontmatter rendering path
    # (_render_note) has no transport exposing it any more — only
    # GatewayApplication.create_chat_export_note calls it internally — but
    # its behaviour must still be pinned directly.
    created = inbox_service.create_inbox_note(
        inbox_root=inbox_root,
        title="Gateway smoke test",
        content="# Gateway smoke test\n",
        frontmatter=None,
        timezone=TOKYO,
    )
    assert created.relative == "Gateway smoke test.md"
    assert (inbox_root / "Gateway smoke test.md").read_text(encoding="utf-8") == (
        "# Gateway smoke test\n"
    )


def test_create_note_with_frontmatter(inbox_root: Path) -> None:
    inbox_service.create_inbox_note(
        inbox_root=inbox_root,
        title="ChatGPTとObsidian Vaultの連携",
        content="# ChatGPTとObsidian Vaultの連携\n\n本文。\n",
        frontmatter={"tags": ["chatgpt", "obsidian"], "source": "chatgpt"},
        timezone=TOKYO,
    )
    written = (inbox_root / "ChatGPTとObsidian Vaultの連携.md").read_text(encoding="utf-8")
    assert written.startswith("---\n")
    assert "tags:" in written
    assert "source: chatgpt" in written
    assert "# ChatGPTとObsidian Vaultの連携" in written


def test_create_note_with_empty_content_is_still_accepted(inbox_root: Path) -> None:
    inbox_service.create_inbox_note(
        inbox_root=inbox_root,
        title="Empty content still valid",
        content="",
        frontmatter=None,
        timezone=TOKYO,
    )
    assert (inbox_root / "Empty content still valid.md").read_text(encoding="utf-8") == ""


def test_create_note_does_not_overwrite_existing(
    application: GatewayApplication, inbox_root: Path
) -> None:
    (inbox_root / "Duplicate.md").write_text("original\n", encoding="utf-8")

    created = application.create_inbox_note(title="Duplicate", content="new content\n")
    assert created.path == "00_Inbox/ChatGPT/Duplicate-2.md"
    assert (inbox_root / "Duplicate.md").read_text(encoding="utf-8") == "original\n"
    assert (inbox_root / "Duplicate-2.md").read_text(encoding="utf-8") == "new content\n"


def test_create_note_sequence_numbers_increment(
    application: GatewayApplication, inbox_root: Path
) -> None:
    for _ in range(3):
        application.create_inbox_note(title="Repeat", content="x\n")

    names = sorted(p.name for p in inbox_root.glob("Repeat*.md"))
    assert names == ["Repeat-2.md", "Repeat-3.md", "Repeat.md"]


def test_create_note_mode_is_0o644(application: GatewayApplication, inbox_root: Path) -> None:
    # Access-control policy (app/services/inbox_service.py's
    # _CREATED_NOTE_MODE), not an incidental default: the vault is a bind
    # mount shared with the host's own Obsidian, and every other note in it
    # is ordinarily 0o644.
    application.create_inbox_note(title="Mode Check", content="x\n")
    assert stat.S_IMODE((inbox_root / "Mode Check.md").stat().st_mode) == 0o644


def test_create_note_mode_is_0o644_even_under_a_restrictive_umask(
    application: GatewayApplication, inbox_root: Path
) -> None:
    # os.fchmod (app/services/inbox_service.py's _write_temp_bytes) sets the
    # mode explicitly, so the process umask must never affect it — unlike a
    # plain os.open(..., mode) create, which the umask does modify.
    old_umask = os.umask(0o077)
    try:
        application.create_inbox_note(title="Umask Check", content="x\n")
    finally:
        os.umask(old_umask)
    assert stat.S_IMODE((inbox_root / "Umask Check.md").stat().st_mode) == 0o644


def test_create_note_conflict_does_not_change_the_existing_files_mode(
    application: GatewayApplication, inbox_root: Path
) -> None:
    existing = inbox_root / "Duplicate.md"
    existing.write_text("original\n", encoding="utf-8")
    existing.chmod(0o600)

    application.create_inbox_note(title="Duplicate", content="new content\n")
    assert stat.S_IMODE(existing.stat().st_mode) == 0o600
    assert stat.S_IMODE((inbox_root / "Duplicate-2.md").stat().st_mode) == 0o644


def test_create_note_leaves_no_temp_files_behind(
    application: GatewayApplication, inbox_root: Path
) -> None:
    application.create_inbox_note(title="No leftovers", content="x\n")
    leftovers = [p for p in inbox_root.iterdir() if p.name.startswith(".tmp-")]
    assert leftovers == []


def test_create_note_sanitises_forbidden_filename_characters(
    application: GatewayApplication,
) -> None:
    created = application.create_inbox_note(title="a/b:c*d", content="x\n")
    assert created.path == "00_Inbox/ChatGPT/a-b-c-d.md"


def test_create_note_rejects_reserved_windows_name(application: GatewayApplication) -> None:
    with pytest.raises(InvalidTitleError):
        application.create_inbox_note(title="CON", content="x\n")


def test_create_note_rejects_empty_title_after_sanitising(
    application: GatewayApplication,
) -> None:
    with pytest.raises(InvalidTitleError):
        application.create_inbox_note(title="...", content="x\n")


def test_create_note_with_structured_export(
    application: GatewayApplication, inbox_root: Path
) -> None:
    from app.models import ChatExport

    application.create_chat_export_note(
        title="Structured export test",
        export=ChatExport(mode="summary", tldr=["ok"], decisions=["d1"]),
    )
    written = (inbox_root / "Structured export test.md").read_text(encoding="utf-8")
    assert "export_mode: summary" in written
    assert "## 決定事項" in written
    assert "- d1" in written


# --- Verbatim/structure-preserving code content (docs/adr/0009-*.md) -----------


def test_create_note_with_legacy_string_steps_still_writes_a_plain_numbered_list(
    application: GatewayApplication, inbox_root: Path
) -> None:
    from app.models import ChatExport

    application.create_chat_export_note(
        title="Legacy string steps test",
        export=ChatExport(mode="procedure", tldr=["ok"], steps=["first", "second"]),
    )
    written = (inbox_root / "Legacy string steps test.md").read_text(encoding="utf-8")
    assert "1. first\n2. second" in written
    assert "```" not in written


def test_create_note_with_rich_step_writes_a_code_fence(
    application: GatewayApplication, inbox_root: Path
) -> None:
    from app.models import ChatExport

    application.create_chat_export_note(
        title="Rich step test",
        export=ChatExport(
            mode="procedure",
            tldr=["ok"],
            steps=[
                {
                    "blocks": [
                        {"type": "text", "content": "設定ファイルを開く。"},
                        {"type": "code", "language": "yaml", "content": "a: b"},
                    ]
                }
            ],
        ),
    )
    written = (inbox_root / "Rich step test.md").read_text(encoding="utf-8")
    assert "```yaml" in written
    assert "a: b" in written


def test_create_note_with_top_level_code_blocks(
    application: GatewayApplication, inbox_root: Path
) -> None:
    from app.models import ChatExport

    application.create_chat_export_note(
        title="Top-level code blocks test",
        export=ChatExport(
            tldr=["ok"],
            code_blocks=[{"type": "code", "label": "compose.yaml", "content": "a: b"}],
        ),
    )
    written = (inbox_root / "Top-level code blocks test.md").read_text(encoding="utf-8")
    assert "## コード" in written
    assert "compose.yaml" in written
    assert "```\na: b\n```" in written


def test_create_note_with_code_first_step_is_rejected(
    application: GatewayApplication, inbox_root: Path
) -> None:
    from app.models import ChatExport

    with pytest.raises(ValidationError):
        application.create_chat_export_note(
            title="Code-first step test",
            export=ChatExport(
                mode="procedure",
                tldr=["ok"],
                steps=[{"blocks": [{"type": "code", "content": "x"}]}],
            ),
        )
    assert not (inbox_root / "Code-first step test.md").exists()


def test_create_note_without_code_blocks_never_writes_a_code_heading(
    application: GatewayApplication, inbox_root: Path
) -> None:
    from app.models import ChatExport

    application.create_chat_export_note(
        title="No code section test", export=ChatExport(tldr=["ok"])
    )
    written = (inbox_root / "No code section test.md").read_text(encoding="utf-8")
    assert "## コード" not in written


def test_create_note_with_table_in_a_body_field(
    application: GatewayApplication, inbox_root: Path
) -> None:
    # docs/adr/0011-*.md end-to-end: a table inside a mode-specific body
    # field renders as GFM Markdown under that field's own heading, not a
    # separate section.
    from app.models import ChatExport

    application.create_chat_export_note(
        title="Table body test",
        export=ChatExport(
            mode="technical",
            tldr=["ok"],
            design=[
                "案Aを採用した",
                {
                    "type": "table",
                    "label": "案の比較",
                    "headers": ["案", "長所"],
                    "rows": [["A", "速い"]],
                },
            ],
        ),
    )
    written = (inbox_root / "Table body test.md").read_text(encoding="utf-8")
    assert "## 設計" in written
    assert "案の比較" in written
    assert "| 案 | 長所 |" in written
    assert "| --- | --- |" in written
    assert "| A | 速い |" in written
    assert "## 表" not in written


def test_create_note_with_code_in_a_body_field(
    application: GatewayApplication, inbox_root: Path
) -> None:
    # docs/adr/0011-*.md end-to-end: CodeBlock (ADR-0009) is reused as a
    # BodyBlock option — a body field's code fence stays in that field's
    # own heading, never moved into the top-level "## コード" section.
    from app.models import ChatExport

    application.create_chat_export_note(
        title="Code body test",
        export=ChatExport(
            mode="technical",
            tldr=["ok"],
            design=[
                "設定を変更する",
                {"type": "code", "language": "yaml", "label": "compose.yaml", "content": "a: b"},
            ],
        ),
    )
    written = (inbox_root / "Code body test.md").read_text(encoding="utf-8")
    assert "## 設計" in written
    assert "compose.yaml" in written
    assert "```yaml\na: b\n```" in written
    assert "## コード" not in written


def test_create_note_with_quote_callout_in_a_body_field(
    application: GatewayApplication, inbox_root: Path
) -> None:
    # docs/adr/0011-*.md end-to-end: an Obsidian callout inside a
    # mode-specific body field renders as "> [!type] title" plus quoted
    # lines, not a separate section.
    from app.models import ChatExport

    application.create_chat_export_note(
        title="Quote body test",
        export=ChatExport(
            mode="technical",
            tldr=["ok"],
            design=[
                "案Aを採用した",
                {
                    "type": "quote",
                    "callout": "warning",
                    "title": "注意",
                    "lines": ["本番では実行しない"],
                },
            ],
        ),
    )
    written = (inbox_root / "Quote body test.md").read_text(encoding="utf-8")
    assert "## 設計" in written
    assert "> [!warning] 注意" in written
    assert "> 本番では実行しない" in written


def test_create_note_with_nested_bullets_and_task_list(
    application: GatewayApplication, inbox_root: Path
) -> None:
    # docs/adr/0011-*.md end-to-end: nesting depth and task-list checkboxes
    # inside a mode-specific body field.
    from app.models import ChatExport

    application.create_chat_export_note(
        title="Nested bullets test",
        export=ChatExport(
            mode="technical",
            tldr=["ok"],
            design=[
                "設定手順",
                {"type": "bullet", "content": "compose.yaml を編集する", "depth": 1},
                {"type": "bullet", "content": "テストを実行する", "checked": False},
                {"type": "bullet", "content": "レビューを依頼する", "checked": True},
            ],
        ),
    )
    written = (inbox_root / "Nested bullets test.md").read_text(encoding="utf-8")
    assert "- 設定手順\n  - compose.yaml を編集する" in written
    assert "- [ ] テストを実行する" in written
    assert "- [x] レビューを依頼する" in written


def test_create_note_with_related_notes(
    application: GatewayApplication, inbox_root: Path
) -> None:
    from app.models import ChatExport

    created = application.create_chat_export_note(
        title="Related notes test",
        export=ChatExport(
            tldr=["ok"],
            related_notes=["Knowledge/PC/GPU/RTX 5070.md", "Knowledge/missing.md"],
        ),
    )
    assert created.related_notes_linked == 1
    assert created.related_notes_skipped == 1
    written = (inbox_root / "Related notes test.md").read_text(encoding="utf-8")
    assert "## 関連ノート\n\n- [[Knowledge/PC/GPU/RTX 5070]]" in written


def test_create_note_with_oversized_related_note_candidate_does_not_block_export(
    application: GatewayApplication, inbox_root: Path
) -> None:
    from app.models import ChatExport

    oversized = "Knowledge/" + "a" * 1020 + ".md"
    created = application.create_chat_export_note(
        title="Oversized related note test",
        export=ChatExport(
            tldr=["ok"], related_notes=[oversized, "Knowledge/PC/GPU/RTX 5070.md"]
        ),
    )
    assert created.related_notes_linked == 1
    assert created.related_notes_skipped == 1
    written = (inbox_root / "Oversized related note test.md").read_text(encoding="utf-8")
    assert "## 関連ノート\n\n- [[Knowledge/PC/GPU/RTX 5070]]" in written


def test_create_note_structured_export_defaults_to_summary(
    application: GatewayApplication, inbox_root: Path
) -> None:
    from app.models import ChatExport

    application.create_chat_export_note(title="Default mode test", export=ChatExport(tldr=["ok"]))
    written = (inbox_root / "Default mode test.md").read_text(encoding="utf-8")
    assert "export_mode: summary" in written


def test_created_note_is_readable_via_get_notes(application: GatewayApplication) -> None:
    created = application.create_inbox_note(title="Round trip", content="# Round trip\n")
    read = application.read_note(path=created.path)
    assert "# Round trip" in read.content


def test_create_note_only_writes_inside_inbox_root(
    application: GatewayApplication, inbox_root: Path
) -> None:
    before = set(os.listdir(inbox_root.parent))
    application.create_inbox_note(title="Contained", content="x\n")
    after = set(os.listdir(inbox_root.parent))
    assert before == after  # nothing written to the vault root, only inside 00_Inbox/ChatGPT


# --- append_inbox_note -------------------------------------------------------

REJECTED_APPEND_PATHS = [
    "../secret.md",
    "../../.obsidian/config",
    "%2e%2e%2fsecret.md",
    "%252e%252e%252fsecret.md",
    "..\\secret.md",
    "/vault/secret.md",
    "C:\\secret.md",
    "00_Inbox/ChatGPT/.hidden.md",
    "not_markdown.txt",
]


def test_append_appends_without_overwriting_lf(
    application: GatewayApplication, inbox_root: Path
) -> None:
    (inbox_root / "Note.md").write_text("first\n", encoding="utf-8")
    appended = application.append_inbox_note(
        path="00_Inbox/ChatGPT/Note.md", content="second\n"
    )
    assert appended.path == "00_Inbox/ChatGPT/Note.md"
    assert (inbox_root / "Note.md").read_text(encoding="utf-8") == "first\nsecond\n"
    assert appended.appended_bytes == len(b"second\n")


def test_append_preserves_crlf_line_endings(
    application: GatewayApplication, inbox_root: Path
) -> None:
    (inbox_root / "Crlf.md").write_bytes(b"first\r\n")
    application.append_inbox_note(path="00_Inbox/ChatGPT/Crlf.md", content="second\n")
    assert (inbox_root / "Crlf.md").read_bytes() == b"first\r\nsecond\r\n"


def test_append_inserts_separating_newline_when_missing(
    application: GatewayApplication, inbox_root: Path
) -> None:
    (inbox_root / "NoTrailingNewline.md").write_bytes(b"first")
    application.append_inbox_note(
        path="00_Inbox/ChatGPT/NoTrailingNewline.md", content="second\n"
    )
    assert (inbox_root / "NoTrailingNewline.md").read_bytes() == b"first\nsecond\n"


def test_append_to_empty_note_needs_no_separator(
    application: GatewayApplication, inbox_root: Path
) -> None:
    (inbox_root / "Empty.md").write_bytes(b"")
    appended = application.append_inbox_note(path="00_Inbox/ChatGPT/Empty.md", content="first\n")
    assert (inbox_root / "Empty.md").read_bytes() == b"first\n"
    assert appended.appended_bytes == len(b"first\n")


def test_appended_bytes_equals_the_actual_file_size_increase(
    application: GatewayApplication, inbox_root: Path
) -> None:
    note = inbox_root / "SizeDelta.md"
    note.write_text("first", encoding="utf-8")  # no trailing newline
    before_size = note.stat().st_size

    appended = application.append_inbox_note(
        path="00_Inbox/ChatGPT/SizeDelta.md", content="second"
    )
    after_size = note.stat().st_size
    assert appended.appended_bytes == after_size - before_size


def test_append_preserves_file_mode(application: GatewayApplication, inbox_root: Path) -> None:
    note = inbox_root / "ModeCheck.md"
    note.write_text("first\n", encoding="utf-8")
    note.chmod(0o640)

    application.append_inbox_note(path="00_Inbox/ChatGPT/ModeCheck.md", content="second\n")
    assert stat.S_IMODE(note.stat().st_mode) == 0o640


def test_append_preserves_a_0o600_file_mode(
    application: GatewayApplication, inbox_root: Path
) -> None:
    # A distinct mode from test_append_preserves_file_mode's 0o640: append
    # must preserve whatever the target already has, not normalise toward
    # either create's 0o644 policy or any other fixed value.
    note = inbox_root / "StrictModeCheck.md"
    note.write_text("first\n", encoding="utf-8")
    note.chmod(0o600)

    application.append_inbox_note(path="00_Inbox/ChatGPT/StrictModeCheck.md", content="second\n")
    assert stat.S_IMODE(note.stat().st_mode) == 0o600


def test_append_leaves_no_temp_files_behind(
    application: GatewayApplication, inbox_root: Path
) -> None:
    (inbox_root / "Clean.md").write_text("x\n", encoding="utf-8")
    application.append_inbox_note(path="00_Inbox/ChatGPT/Clean.md", content="y\n")
    leftovers = [p for p in inbox_root.iterdir() if p.name.startswith(".tmp-")]
    assert leftovers == []


def test_append_only_writes_inside_inbox_root(
    application: GatewayApplication, inbox_root: Path
) -> None:
    (inbox_root / "Contained.md").write_text("x\n", encoding="utf-8")
    before = set(os.listdir(inbox_root.parent))
    application.append_inbox_note(path="00_Inbox/ChatGPT/Contained.md", content="y\n")
    after = set(os.listdir(inbox_root.parent))
    assert before == after


def test_append_rejects_empty_content(
    application: GatewayApplication, inbox_root: Path
) -> None:
    (inbox_root / "Target.md").write_text("x\n", encoding="utf-8")
    with pytest.raises(ValidationError):
        application.append_inbox_note(path="00_Inbox/ChatGPT/Target.md", content="")


def test_append_rejects_whitespace_only_content(
    application: GatewayApplication, inbox_root: Path
) -> None:
    (inbox_root / "Target.md").write_text("x\n", encoding="utf-8")
    with pytest.raises(ValidationError):
        application.append_inbox_note(path="00_Inbox/ChatGPT/Target.md", content="   \n  ")


def test_append_rejects_missing_note(application: GatewayApplication) -> None:
    with pytest.raises(NoteNotFoundError):
        application.append_inbox_note(path="00_Inbox/ChatGPT/does-not-exist.md", content="x\n")


@pytest.mark.parametrize("raw", REJECTED_APPEND_PATHS)
def test_append_rejects_malicious_or_invalid_paths(
    application: GatewayApplication, raw: str
) -> None:
    with pytest.raises(
        (InvalidPathError, PathOutsideVaultError, NoteNotFoundError, InvalidFileTypeError)
    ):
        application.append_inbox_note(path=raw, content="x\n")


def test_append_rejects_path_outside_inbox(application: GatewayApplication) -> None:
    with pytest.raises(PathOutsideVaultError):
        application.append_inbox_note(path="Knowledge/no_frontmatter.md", content="x\n")


def test_append_rejects_subdirectory_of_inbox(
    application: GatewayApplication, inbox_root: Path
) -> None:
    subdir = inbox_root / "Sub"
    subdir.mkdir()
    (subdir / "Nested.md").write_text("x\n", encoding="utf-8")

    with pytest.raises(InvalidPathError):
        application.append_inbox_note(path="00_Inbox/ChatGPT/Sub/Nested.md", content="y\n")


def test_append_rejects_symlinked_note(
    application: GatewayApplication, inbox_root: Path, vault_root: Path
) -> None:
    outside_target = vault_root.parent / "outside-secret.md"
    outside_target.write_text("secret\n", encoding="utf-8")
    (inbox_root / "Link.md").symlink_to(outside_target)

    with pytest.raises(InvalidPathError):
        application.append_inbox_note(path="00_Inbox/ChatGPT/Link.md", content="x\n")
    assert outside_target.read_text(encoding="utf-8") == "secret\n"


def test_append_rejects_already_oversized_note(
    application: GatewayApplication, inbox_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.config import get_settings

    note = inbox_root / "AlreadyBig.md"
    note.write_text("x" * 2000, encoding="utf-8")
    note.chmod(0o640)
    before = note.stat()

    monkeypatch.setenv("MAX_NOTE_SIZE_BYTES", "1024")
    get_settings.cache_clear()
    try:
        with pytest.raises(FileTooLargeError):
            GatewayApplication(get_settings()).append_inbox_note(
                path="00_Inbox/ChatGPT/AlreadyBig.md", content="y\n"
            )
        after = note.stat()
        assert note.read_text(encoding="utf-8") == "x" * 2000
        # A rejected append must be a complete no-op on the target: same
        # mode, same inode (never replaced), same mtime (never rewritten).
        assert stat.S_IMODE(after.st_mode) == 0o640
        assert after.st_ino == before.st_ino
        assert after.st_mtime_ns == before.st_mtime_ns
    finally:
        get_settings.cache_clear()


def test_append_rejects_when_result_would_exceed_size_limit(
    application: GatewayApplication, inbox_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.config import get_settings

    (inbox_root / "NearLimit.md").write_text("x" * 900, encoding="utf-8")
    monkeypatch.setenv("MAX_NOTE_SIZE_BYTES", "1024")
    get_settings.cache_clear()
    try:
        with pytest.raises(FileTooLargeError):
            GatewayApplication(get_settings()).append_inbox_note(
                path="00_Inbox/ChatGPT/NearLimit.md", content="y" * 900
            )
        assert (inbox_root / "NearLimit.md").read_text(encoding="utf-8") == "x" * 900
    finally:
        get_settings.cache_clear()


def test_append_lock_file_being_a_symlink_is_rejected_and_target_unchanged(
    application: GatewayApplication, inbox_root: Path, vault_root: Path
) -> None:
    outside_target = vault_root.parent / "lock-escape-secret.md"
    outside_target.write_text("do not touch\n", encoding="utf-8")
    (inbox_root / ".append.lock").symlink_to(outside_target)
    (inbox_root / "Note.md").write_text("original\n", encoding="utf-8")

    with pytest.raises(InvalidPathError) as excinfo:
        application.append_inbox_note(path="00_Inbox/ChatGPT/Note.md", content="x\n")
    assert outside_target.read_text(encoding="utf-8") == "do not touch\n"
    assert (inbox_root / "Note.md").read_text(encoding="utf-8") == "original\n"
    assert str(vault_root) not in excinfo.value.message


def test_append_lock_file_not_a_regular_file_is_rejected(
    application: GatewayApplication, inbox_root: Path
) -> None:
    os.mkfifo(inbox_root / ".append.lock")
    (inbox_root / "Note.md").write_text("original\n", encoding="utf-8")

    with pytest.raises(InvalidFileTypeError):
        application.append_inbox_note(path="00_Inbox/ChatGPT/Note.md", content="x\n")
    assert (inbox_root / "Note.md").read_text(encoding="utf-8") == "original\n"


def test_append_detects_concurrent_modification_and_leaves_target_unchanged(
    application: GatewayApplication, inbox_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A modification landing on the target between this request's read and

    its pre-replace identity check (e.g. LiveSync writing the same file
    concurrently) must be detected — the append must not silently discard
    that other write.
    """
    note = inbox_root / "RaceTarget.md"
    note.write_text("original\n", encoding="utf-8")

    original_write_temp_bytes = inbox_service._write_temp_bytes

    def write_then_mutate(*args, **kwargs):
        note.write_text("original\nsomeone else wrote this\n", encoding="utf-8")
        return original_write_temp_bytes(*args, **kwargs)

    monkeypatch.setattr(inbox_service, "_write_temp_bytes", write_then_mutate)

    with pytest.raises(NoteModifiedError):
        application.append_inbox_note(path="00_Inbox/ChatGPT/RaceTarget.md", content="appended\n")
    assert note.read_text(encoding="utf-8") == "original\nsomeone else wrote this\n"


def test_concurrent_appends_do_not_lose_content(
    application: GatewayApplication, inbox_root: Path
) -> None:
    (inbox_root / "Concurrent.md").write_text("start\n", encoding="utf-8")
    markers = [f"line-{i}\n" for i in range(8)]

    def append_one(marker: str) -> None:
        application.append_inbox_note(path="00_Inbox/ChatGPT/Concurrent.md", content=marker)

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(append_one, markers))  # any exception re-raises here

    final = (inbox_root / "Concurrent.md").read_text(encoding="utf-8")
    assert final.startswith("start\n")
    for marker in markers:
        assert final.count(marker) == 1


def test_append_response_never_contains_an_absolute_path(
    application: GatewayApplication, inbox_root: Path, vault_root: Path
) -> None:
    (inbox_root / "PathCheck.md").write_text("x\n", encoding="utf-8")
    appended = application.append_inbox_note(path="00_Inbox/ChatGPT/PathCheck.md", content="y\n")
    assert str(vault_root) not in appended.model_dump_json()


def test_append_note_content_is_readable_via_get_notes(
    application: GatewayApplication, inbox_root: Path
) -> None:
    (inbox_root / "RoundTrip.md").write_text("# Round trip\n\n", encoding="utf-8")
    application.append_inbox_note(
        path="00_Inbox/ChatGPT/RoundTrip.md", content="more content\n"
    )
    read = application.read_note(path="00_Inbox/ChatGPT/RoundTrip.md")
    assert "more content" in read.content


# --- append lock timeout: app/services/inbox_service.py's _acquire_exclusive_lock ---


def test_acquire_exclusive_lock_retries_eagain_and_eacces_then_succeeds(monkeypatch) -> None:
    # EACCES is the one flock() can raise on contention that BlockingIOError
    # alone would not catch (BlockingIOError only maps EAGAIN/EWOULDBLOCK/
    # EALREADY/EINPROGRESS) — both must be retried, not just EAGAIN.
    pending_errnos = [errno.EAGAIN, errno.EACCES]

    def fake_flock(_fd: int, _flags: int) -> None:
        if pending_errnos:
            raise OSError(pending_errnos.pop(0), "locked")

    monkeypatch.setattr(inbox_service.fcntl, "flock", fake_flock)
    monkeypatch.setattr(inbox_service, "_LOCK_POLL_INTERVAL_SECONDS", 0.001)

    inbox_service._acquire_exclusive_lock(-1)  # fd is unused by the fake
    assert pending_errnos == []


def test_acquire_exclusive_lock_reraises_unrelated_oserror_immediately(monkeypatch) -> None:
    def fake_flock(_fd: int, _flags: int) -> None:
        raise OSError(errno.EIO, "disk error")

    monkeypatch.setattr(inbox_service.fcntl, "flock", fake_flock)
    # A large timeout would make this test slow if the retry loop swallowed
    # EIO instead of re-raising it immediately.
    monkeypatch.setattr(inbox_service, "_LOCK_TIMEOUT_SECONDS", 30.0)

    with pytest.raises(OSError) as excinfo:
        inbox_service._acquire_exclusive_lock(-1)
    assert excinfo.value.errno == errno.EIO
    assert not isinstance(excinfo.value, InboxLockTimeoutError)


def test_acquire_exclusive_lock_times_out_on_sustained_contention(monkeypatch) -> None:
    monkeypatch.setattr(inbox_service, "_LOCK_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(inbox_service, "_LOCK_POLL_INTERVAL_SECONDS", 0.01)

    def always_blocked(_fd: int, _flags: int) -> None:
        raise OSError(errno.EAGAIN, "locked")

    monkeypatch.setattr(inbox_service.fcntl, "flock", always_blocked)

    start = time.monotonic()
    with pytest.raises(InboxLockTimeoutError):
        inbox_service._acquire_exclusive_lock(-1)
    # Bounded: the deadline is set before the first attempt, so the actual
    # wait should not run away past the configured timeout.
    assert time.monotonic() - start < 1.0


# --- append lock timeout: end-to-end, against a real cross-process flock ---


def test_append_times_out_when_lock_held_by_another_process_then_recovers(
    application: GatewayApplication,
    inbox_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    vault_root: Path,
    hold_flock_in_subprocess,
) -> None:
    monkeypatch.setattr(inbox_service, "_LOCK_TIMEOUT_SECONDS", 0.3)
    monkeypatch.setattr(inbox_service, "_LOCK_POLL_INTERVAL_SECONDS", 0.02)

    note = inbox_root / "LockedOut.md"
    note.write_text("original\n", encoding="utf-8")
    lock_path = str(inbox_root / ".append.lock")

    ctx = multiprocessing.get_context("spawn")
    acquired = ctx.Event()
    holder = ctx.Process(target=hold_flock_in_subprocess, args=(lock_path, 5.0, acquired))
    holder.start()
    try:
        assert acquired.wait(timeout=5), "holder process never acquired the lock"
        start = time.monotonic()
        with pytest.raises(InboxLockTimeoutError) as excinfo:
            application.append_inbox_note(path="00_Inbox/ChatGPT/LockedOut.md", content="x\n")
        elapsed = time.monotonic() - start
    finally:
        holder.terminate()
        holder.join(timeout=5)

    # Bounded by the timeout, not by the holder's 5-second hold.
    assert elapsed < 3.0

    # No absolute path, fd, or inode leaked into the exception message.
    message = excinfo.value.message
    assert str(vault_root) not in message
    assert "Errno" not in message
    assert ".append.lock" not in message

    # The target must be untouched, and no temp file left behind, while the
    # lock was contended.
    assert note.read_text(encoding="utf-8") == "original\n"
    leftovers = [p for p in inbox_root.iterdir() if p.name.startswith(".tmp-")]
    assert leftovers == []

    # Once the holder process has exited (which releases its flock), the
    # lock must not be stuck: the next append succeeds normally.
    retry = application.append_inbox_note(path="00_Inbox/ChatGPT/LockedOut.md", content="second\n")
    assert retry is not None
    assert note.read_text(encoding="utf-8") == "original\nsecond\n"


def test_inbox_lock_timeout_is_logged_by_the_rest_exception_handler_with_a_reason(
    env: None, caplog: pytest.LogCaptureFixture
) -> None:
    """``handle_gateway_error`` (app/main.py) is kept as the last line of
    defence for the uniform error envelope even though REST is health-only
    now (docs/adr/0010-*.md) — this pins that it still logs the gateway
    error code for a real ``GatewayError`` subclass, independent of any
    particular route existing to raise it. ``env`` is required directly
    (not via the ``application``/``client`` fixtures) because importing
    ``app.main`` for the first time in a process runs its module-level
    ``get_settings()`` call.
    """
    from app.main import handle_gateway_error

    exc = InboxLockTimeoutError(log_detail="timed out waiting for the inbox append lock")
    with caplog.at_level("INFO", logger="obsidian_gateway"):
        asyncio.run(handle_gateway_error(None, exc))

    records = [r for r in caplog.records if r.name == "obsidian_gateway"]
    assert any(getattr(r, "code", None) == "INBOX_LOCK_TIMEOUT" for r in records)


# --- find_duplicate_candidates (issue #14) -------------------------------------


def test_find_duplicate_candidates_returns_expected_shape(
    application: GatewayApplication, inbox_root: Path
) -> None:
    (inbox_root / "Existing.md").write_text("---\ntitle: Shared Title\n---\n", encoding="utf-8")
    response = application.find_duplicate_candidates(title="Shared Title")
    assert response.recommendation == "confirm"
    assert response.candidates[0].path == "00_Inbox/ChatGPT/Existing.md"
    assert not hasattr(response.candidates[0], "score")


def test_find_duplicate_candidates_no_match_recommends_create(
    application: GatewayApplication,
) -> None:
    response = application.find_duplicate_candidates(title="Nothing in the empty inbox matches")
    assert response.candidates == []
    assert response.recommendation == "create"


def test_find_duplicate_candidates_rejects_limit_out_of_range(
    application: GatewayApplication,
) -> None:
    # Application-layer re-validation (U7), matching search_notes's pattern
    # — independent of whatever transport-level parameter validation a
    # caller's own request went through first.
    with pytest.raises(ValidationError):
        application.find_duplicate_candidates(title="x", limit=0)
    with pytest.raises(ValidationError):
        application.find_duplicate_candidates(title="x", limit=11)
