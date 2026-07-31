"""Shared fixtures.

Every test runs against a disposable vault built under ``tmp_path`` — never the
committed fixtures directory itself, and never a real vault (AGENTS.md: "Do not
modify an actual Obsidian Vault during automated tests"). The committed fixture
tree under ``tests/fixtures/vault`` holds only plain files; anything that must
not be committed to git (symlinks, huge files) is generated at test time by
:func:`vault_root`.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

FIXTURE_VAULT = Path(__file__).parent / "fixtures" / "vault"
TEST_API_TOKEN = "test-token-0123456789abcdef"  # noqa: S105 - test fixture, not a real secret
INBOX_RELATIVE_PATH = "00_Inbox/ChatGPT"


@pytest.fixture
def vault_root(tmp_path: Path) -> Path:
    """A throwaway copy of the fixture vault, plus generated edge cases."""
    root = tmp_path / "vault"
    shutil.copytree(FIXTURE_VAULT, root)

    # A secret sitting just outside the vault, reachable only via traversal.
    (tmp_path / "secret.md").write_text("# Secret\n\nOutside the vault.\n", encoding="utf-8")

    # A note reached only through a symlink, at two levels: a symlinked file and
    # a symlinked directory. Both must be invisible to search and reads.
    (root / "Knowledge" / "symlinked-note.md").symlink_to(
        root / "Knowledge" / "PC" / "GPU" / "RTX 5070.md"
    )
    symlinked_dir = root / "Knowledge" / "SymlinkedDir"
    symlinked_dir.symlink_to(root / "Knowledge" / "PC", target_is_directory=True)

    # CRLF line endings must survive a read untouched.
    (root / "Knowledge" / "crlf.md").write_bytes(b"# CRLF\r\n\r\nWindows style.\r\n")

    # A note larger than a small MAX_NOTE_SIZE_BYTES, for truncation tests.
    (root / "Knowledge" / "large.md").write_text("x" * 5000, encoding="utf-8")

    return root


@pytest.fixture
def inbox_root(vault_root: Path) -> Path:
    path = vault_root / INBOX_RELATIVE_PATH
    path.mkdir(parents=True, exist_ok=True)
    return path


@pytest.fixture
def env(vault_root: Path, inbox_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point Settings at the throwaway vault and clear the settings cache.

    Import app.config lazily so this fixture works even before app.main has
    ever been imported in the process.
    """
    from app.config import get_settings

    monkeypatch.setenv("API_TOKEN", TEST_API_TOKEN)
    monkeypatch.setenv("VAULT_READ_ROOT", str(vault_root))
    monkeypatch.setenv("VAULT_INBOX_ROOT", str(inbox_root))
    monkeypatch.setenv("VAULT_INBOX_RELATIVE_PATH", INBOX_RELATIVE_PATH)
    monkeypatch.setenv("MAX_SEARCH_RESULTS", "50")
    monkeypatch.setenv("MAX_NOTE_SIZE_BYTES", "1048576")
    monkeypatch.setenv("MAX_REQUEST_BYTES", "2097152")
    monkeypatch.setenv("TZ", "Asia/Tokyo")

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def client(env: None) -> TestClient:
    from app.main import app

    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {TEST_API_TOKEN}"}


@pytest.fixture
def api_token() -> str:
    return TEST_API_TOKEN


def make_symlink_outside(vault_root: Path, target: Path) -> Path:
    """Create a symlink inside the vault pointing at ``target`` (outside it)."""
    link = vault_root / "escape.md"
    link.symlink_to(target)
    return link
