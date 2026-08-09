"""app/runtime.py's dedicated vault-scan capacity limiter.

``search_notes`` and ``get_vault_summary`` walk and read the whole vault; on
MCP they run in a worker thread through ``runtime.vault_scan_limiter``
instead of the default thread pool, so a blocked or slow scan can never
starve ``/health`` of a thread. Checking that the scanning tools are
``async def`` and the rest are plain ``def`` (the ``test_*_functions`` checks
below) proves the *shape* of the fix but not the *isolation* it exists for —
a limiter is easy to wire up wrong (e.g. passed to the wrong call, omitted,
or duplicated into a second instance) in a way that still leaves every
function sync/async as expected. Only a real concurrency test proves
isolation, which is why most of this file drives MCP through
``mcp.call_tool(...)`` and REST's own ``/api/v1/health`` through
``httpx.AsyncClient`` + ``ASGITransport`` (rather than ``TestClient``, which
runs the whole app on a separate thread via a portal and would not reliably
reproduce the production event-loop-plus-worker-threads shape these tests
depend on), in the same event loop so both can be put in the same task group.

REST is health-only now (docs/adr/0010-*.md) — the two full-vault-scanning
REST endpoints that used to share this limiter alongside MCP are gone, so
the isolation this module proves is now "MCP's own scanning tools never
starve /health or each other of a limiter token", not "REST and MCP share
one limiter" (that cross-transport sharing property has no REST side left to
test against).
"""

from __future__ import annotations

import inspect
import threading
import time

import anyio
import httpx
import pytest

from app import runtime
from app.mcp_server import mcp
from app.routers import health

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def test_mcp_scanning_tools_are_offloaded_through_the_dedicated_limiter() -> None:
    # `Tool.is_async` is the SDK's own is_async_callable(fn) result recorded
    # at registration time (mcp/server/mcpserver/tools/base.py), so this
    # asserts the same fact inspect.iscoroutinefunction would, through the
    # manager's public get_tool() accessor rather than reaching for the raw
    # function.
    for name in ("search_notes", "get_vault_summary", "find_duplicate_candidates"):
        assert mcp._tool_manager.get_tool(name).is_async


def test_lightweight_endpoints_are_plain_sync_functions() -> None:
    # Shares FastAPI's default thread pool (app/runtime.py's limiter is only
    # for the vault-scanning MCP tools above) — a plain `def` is enough for
    # it; no dedicated limiter needed.
    assert not inspect.iscoroutinefunction(health.get_health)


def test_lightweight_mcp_tools_are_plain_sync_functions() -> None:
    for name in (
        "get_health",
        "read_note",
        "get_vault_tree",
        "create_inbox_note",
        "append_inbox_note",
    ):
        assert not mcp._tool_manager.get_tool(name).is_async


def test_vault_scan_limiter_capacity_is_two() -> None:
    # A cheap regression guard: every call site (app/mcp_server.py) reaches
    # this one object through `runtime.vault_scan_limiter`, never a name
    # imported out of the module, so there is nowhere for a second,
    # differently-sized limiter to be introduced by accident. The
    # behavioural tests below are what prove every call site actually uses
    # it.
    assert runtime.VAULT_SCAN_CONCURRENCY == 2
    assert runtime.vault_scan_limiter.total_tokens == 2


async def _wait_until(predicate, *, timeout: float = 5.0, interval: float = 0.01) -> None:
    """Poll ``predicate`` cooperatively until it is true or ``timeout`` elapses."""
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError("condition was not met within the timeout")
        await anyio.sleep(interval)


async def test_search_and_summary_share_the_same_limiter(
    env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two blocked MCP search_notes calls must fully occupy the limiter
    (capacity 2) and keep a concurrent get_vault_summary call from starting
    at all — proving the two tools share one limiter rather than each
    getting their own.
    """
    from app import application as application_module

    search_started = [threading.Event(), threading.Event()]
    search_release = threading.Event()
    summary_started = threading.Event()
    next_index = {"n": 0}
    index_lock = threading.Lock()

    original_search = application_module.search_notes
    original_summarise = application_module.summarise_vault

    def blocking_search(*args, **kwargs):
        with index_lock:
            idx = next_index["n"]
            next_index["n"] += 1
        search_started[idx].set()
        search_release.wait(timeout=5)
        return original_search(*args, **kwargs)

    def blocking_summarise(*args, **kwargs):
        summary_started.set()
        return original_summarise(*args, **kwargs)

    monkeypatch.setattr(application_module, "search_notes", blocking_search)
    monkeypatch.setattr(application_module, "summarise_vault", blocking_summarise)

    results: dict[str, object] = {}

    async def run_search(i: int) -> None:
        results[f"search{i}"] = await mcp.call_tool("search_notes", {})

    async def run_summary() -> None:
        results["summary"] = await mcp.call_tool("get_vault_summary", {})

    async with anyio.create_task_group() as tg:
        tg.start_soon(run_search, 0)
        tg.start_soon(run_search, 1)
        await _wait_until(lambda: search_started[0].is_set() and search_started[1].is_set())

        tg.start_soon(run_summary)
        # Both limiter tokens are held by the two blocked searches, so
        # summary must not be able to start yet.
        await anyio.sleep(0.2)
        assert not summary_started.is_set(), (
            "summary started while both limiter tokens were held by search"
        )

        search_release.set()

    assert summary_started.is_set()
    for key in ("search0", "search1", "summary"):
        assert results[key].is_error is False


async def test_limiter_token_is_released_after_a_scan_raises(
    env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A scan that raises must still give its limiter token back — otherwise
    every failure would permanently shrink the effective concurrency.
    """
    from app import application as application_module
    from mcp.shared.exceptions import MCPError

    def failing_summarise(*args, **kwargs):
        raise RuntimeError("boom")

    original_summarise = application_module.summarise_vault
    monkeypatch.setattr(application_module, "summarise_vault", failing_summarise)

    # VAULT_SCAN_CONCURRENCY calls that all fail — if any token leaked,
    # capacity would now be permanently reduced. mcp.call_tool raises on
    # error rather than returning an is_error=True result.
    for _ in range(2):
        with pytest.raises(MCPError):
            await mcp.call_tool("get_vault_summary", {})

    # Restore only this one attribute — monkeypatch.undo() would also revert
    # the env fixture's env-var patches, since fixtures share one MonkeyPatch
    # instance per test.
    monkeypatch.setattr(application_module, "summarise_vault", original_summarise)

    # Bounded wait: if a token had leaked, this would hang past the
    # limiter's default (infinite) wait — the timeout would surface it as a
    # failure rather than a hang.
    with anyio.move_on_after(5) as scope:
        result = await mcp.call_tool("get_vault_summary", {})
    assert not scope.cancelled_caught, "a real request never got a limiter token back"
    assert result.is_error is False


async def test_health_stays_responsive_while_mcp_scans_are_blocked(
    env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Before this fix, MCP's scanning tools ran on anyio's default
    thread-pool limiter — the same 40-token pool /health (a plain `def` REST
    handler) shares — so 2 or 3 concurrent MCP scans alone would never
    contend with it. Shrinking the default pool to the dedicated limiter's
    own size (2) reproduces that contention on demand: before the fix, two
    blocked MCP scans exhaust the shrunk default pool and /health cannot get
    a thread until the stub below is released; after the fix, MCP scans go
    through runtime.vault_scan_limiter instead, the shrunk default pool
    stays free, and /health returns immediately.

    The stub's own ``release.wait(timeout=...)`` is a hang-safety fallback
    only, never the thing that actually frees a thread for /health — it is
    deliberately much longer than the bounded wait on the /health request
    below, so the two windows cannot race each other into a false pass; the
    real unblock is always this test's own ``release.set()`` in ``finally``.
    """
    from app import application as application_module
    from app.main import app

    started = [threading.Event(), threading.Event()]
    release = threading.Event()
    next_index = {"n": 0}
    index_lock = threading.Lock()

    original_search = application_module.search_notes

    def blocking_search(*args, **kwargs):
        with index_lock:
            idx = next_index["n"]
            next_index["n"] += 1
        started[idx].set()
        release.wait(timeout=30)
        return original_search(*args, **kwargs)

    monkeypatch.setattr(application_module, "search_notes", blocking_search)

    default_limiter = anyio.to_thread.current_default_thread_limiter()
    original_tokens = default_limiter.total_tokens
    default_limiter.total_tokens = 2
    try:
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            results: dict[str, object] = {}

            async def run_mcp_search(i: int) -> None:
                results[f"mcp{i}"] = await mcp.call_tool("search_notes", {})

            async with anyio.create_task_group() as tg:
                tg.start_soon(run_mcp_search, 0)
                tg.start_soon(run_mcp_search, 1)
                await _wait_until(lambda: started[0].is_set() and started[1].is_set())

                # release.set() must run *before* this `async with tg:` block
                # exits, success or failure: to_thread.run_sync's default
                # (cancellable=False) means a cancelled child task does not
                # actually unblock its worker thread — __aexit__ would just
                # sit waiting for it, past this test's own fail_after, all
                # the way out to the stub's 30s safety net. A `finally`
                # placed after this block runs too late to prevent that.
                try:
                    with anyio.fail_after(2):
                        health_response = await client.get("/api/v1/health")
                    assert health_response.status_code == 200
                finally:
                    release.set()
    finally:
        release.set()
        default_limiter.total_tokens = original_tokens

    assert results["mcp0"].is_error is False
    assert results["mcp1"].is_error is False
