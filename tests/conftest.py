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
from collections.abc import Iterator
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
    monkeypatch.setenv("MCP_ALLOWED_HOSTS", "testserver,127.0.0.1:*,localhost:*")
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
def application(env: None):
    """A :class:`~app.application.GatewayApplication` against the same
    throwaway vault/inbox the ``client``/``env`` fixtures point at.

    The single shared definition — most test modules used to build a
    ``TestClient`` and drive REST instead; this is the direct,
    transport-neutral equivalent.
    """
    from app.application import GatewayApplication
    from app.config import get_settings

    return GatewayApplication(get_settings())


@pytest.fixture(scope="session")
def mcp_client(tmp_path_factory: pytest.TempPathFactory) -> Iterator[TestClient]:
    """A ``TestClient`` for the real, shared ``/mcp`` endpoint, with its
    lifespan entered exactly once for the whole test session.

    ``client`` above deliberately never enters the app's lifespan (existing
    REST tests never needed the MCP session manager running); any request to
    ``/mcp`` does, since ``mcp.session_manager.run()`` only starts inside
    that lifespan. But ``app.mcp_server.mcp`` is a module-level singleton
    built once at import time — matching production and MCP_IMPLEMENTATION
    _PLAN section 9's ordering requirement — and its session manager's
    ``run()`` can only be entered once per process (verified against the
    installed SDK: a second call raises ``RuntimeError``). So unlike
    ``env``/``client``, which every test gets its own fresh copy of via
    ``tmp_path``, every test needing the real mounted ``/mcp`` endpoint has
    to share this one session-scoped entry rather than one per test.

    Sets up its own environment directly (not the function-scoped ``env``
    fixture, which a session-scoped fixture can't depend on) against a vault
    built once for the session. test_mcp_lifespan.py tests the one-shot
    lifecycle mechanics themselves against an independent, throwaway
    ``MCPServer`` — precisely because this fixture's constraint means the
    shared singleton can't be reused to demonstrate them.
    """
    vault_root = tmp_path_factory.mktemp("mcp-session-vault") / "vault"
    shutil.copytree(FIXTURE_VAULT, vault_root)
    inbox_root = vault_root / INBOX_RELATIVE_PATH
    inbox_root.mkdir(parents=True, exist_ok=True)

    mp = pytest.MonkeyPatch()
    mp.setenv("API_TOKEN", TEST_API_TOKEN)
    mp.setenv("MCP_ALLOWED_HOSTS", "testserver,127.0.0.1:*,localhost:*")
    mp.setenv("VAULT_READ_ROOT", str(vault_root))
    mp.setenv("VAULT_INBOX_ROOT", str(inbox_root))
    mp.setenv("VAULT_INBOX_RELATIVE_PATH", INBOX_RELATIVE_PATH)
    mp.setenv("MAX_SEARCH_RESULTS", "50")
    mp.setenv("MAX_NOTE_SIZE_BYTES", "1048576")
    mp.setenv("MAX_REQUEST_BYTES", "2097152")
    mp.setenv("TZ", "Asia/Tokyo")

    from app.config import get_settings

    get_settings.cache_clear()
    from app.main import app

    with TestClient(app, raise_server_exceptions=False) as c:
        yield c

    mp.undo()
    get_settings.cache_clear()


@pytest.fixture
def mcp_headers(api_token: str) -> dict[str, str]:
    """Headers a legacy-era MCP request needs. Modern (2026-07-28) requests
    additionally need ``Mcp-Method``/``Mcp-Name`` headers and a
    ``params._meta`` envelope — see tests/test_mcp_protocol.py's
    ``_modern_meta``/``_modern_headers`` helpers.
    """
    return {
        "Authorization": f"Bearer {api_token}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }


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


def _hold_flock_in_subprocess(lock_path: str, hold_seconds: float, acquired=None) -> None:
    """Hold an exclusive flock on ``lock_path`` from a separate process.

    Module-level (not a closure) and taking only picklable arguments so
    ``multiprocessing.get_context("spawn").Process`` can target it directly
    — "spawn", not the default "fork", because the test process is
    multi-threaded (pytest-anyio's worker threads) and CPython's own docs
    warn that fork()ing a multi-threaded process can deadlock. Used by the
    append lock timeout tests (tests/test_inbox.py, tests/test_mcp_tools.py)
    to exercise real cross-process flock contention rather than two file
    descriptors in the same process, which would not catch every platform
    difference in flock's semantics.

    ``acquired``, if given, is a ``multiprocessing.Event`` set the instant
    the lock is held — callers must wait on it rather than sleeping a guessed
    duration, since "spawn" starts a fresh interpreter and its startup time
    is not bounded tightly enough for a fixed sleep to be reliable.

    Exposed to test files only via the ``hold_flock_in_subprocess`` fixture
    below, never via a direct ``from tests.conftest import ...`` — ``tests``
    has no ``__init__.py``, so that import only resolves when the current
    directory happens to be on ``sys.path`` (e.g. ``python -m pytest``); a
    bare ``pytest`` invocation (what CI actually runs) does not add it,
    and fails with ``ModuleNotFoundError: No module named 'tests'``.
    pytest's own conftest.py resolution has no such dependency on
    invocation style, so a fixture that returns this function is not just
    a style preference — it is what makes both test collection and the
    spawned subprocess's own re-import of this function (multiprocessing
    pickles a target by module + qualified name) work the same way
    regardless of how pytest was invoked.
    """
    import fcntl
    import os
    import time

    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        if acquired is not None:
            acquired.set()
        time.sleep(hold_seconds)
    finally:
        os.close(fd)


@pytest.fixture
def hold_flock_in_subprocess():
    """Fixture indirection for :func:`_hold_flock_in_subprocess` — see that
    function's docstring for why a direct cross-file import is unsafe here.
    """
    return _hold_flock_in_subprocess
