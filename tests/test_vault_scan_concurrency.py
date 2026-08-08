"""app/runtime.py's dedicated vault-scan capacity limiter.

``search`` and ``get_vault_summary`` walk and read the whole vault; on both
transports they run in a worker thread through ``runtime.vault_scan_limiter``
instead of the default thread pool, so a blocked or slow scan can never
starve ``/health`` (or any other lightweight request, on either transport) of
a thread — and so REST and MCP scans are bounded *together*, not each given
an independent allowance. Checking that the scanning handlers/tools are
``async def`` and the rest are plain ``def`` (the ``test_*_functions``
checks below) proves the *shape* of the fix but not the *isolation* it
exists for — a limiter is easy to wire up wrong (e.g. passed to the wrong
call, omitted, or duplicated into a second instance) in a way that still
leaves every function sync/async as expected. Only a real concurrency test
proves isolation, which is why most of this file drives REST end-to-end
through ``httpx.AsyncClient`` + ``ASGITransport`` (rather than
``TestClient``, which runs the whole app on a separate thread via a portal
and would not reliably reproduce the production event-loop-plus-worker-
threads shape these tests depend on) and drives MCP through
``mcp.call_tool(...)`` in the same event loop, so both transports can be
put in the same task group.
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
from app.routers import health, inbox, notes, search, vault

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def test_vault_scanning_endpoints_are_offloaded_through_the_dedicated_limiter() -> None:
    # Auxiliary check only: proves the shape (async, so they can await
    # run_sync), not the isolation — see this module's docstring.
    assert inspect.iscoroutinefunction(search.search)
    assert inspect.iscoroutinefunction(vault.get_vault_summary)
    # find_duplicate_candidates (issue #14) scans the inbox directory the
    # same way — no note-count cap of its own, so it shares this limiter
    # rather than the default thread pool.
    assert inspect.iscoroutinefunction(inbox.find_duplicate_candidates)


def test_mcp_scanning_tools_are_offloaded_through_the_dedicated_limiter() -> None:
    # Same shape check as the REST handlers above, for the MCP tools that
    # also do a full-vault scan. `Tool.is_async` is the SDK's own
    # is_async_callable(fn) result recorded at registration time
    # (mcp/server/mcpserver/tools/base.py), so this asserts the same fact
    # inspect.iscoroutinefunction would, through the manager's public
    # get_tool() accessor rather than reaching for the raw function.
    for name in ("search_notes", "get_vault_summary", "find_duplicate_candidates"):
        assert mcp._tool_manager.get_tool(name).is_async


def test_lightweight_endpoints_are_plain_sync_functions() -> None:
    # These share FastAPI's default thread pool (app/runtime.py's limiter is
    # only for the two vault-scanning handlers above) — a plain `def` is
    # enough for them; no dedicated limiter needed.
    assert not inspect.iscoroutinefunction(health.get_health)
    assert not inspect.iscoroutinefunction(notes.read_note)
    assert not inspect.iscoroutinefunction(vault.get_vault_tree)
    assert not inspect.iscoroutinefunction(inbox.create_note)
    assert not inspect.iscoroutinefunction(inbox.append_note)


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
    # A cheap regression guard: every call site (app/routers/search.py,
    # app/routers/vault.py, app/mcp_server.py) reaches this one object
    # through `runtime.vault_scan_limiter`, never a name imported out of the
    # module, so there is nowhere for a second, differently-sized limiter to
    # be introduced by accident. The behavioural tests below are what prove
    # every call site actually uses it.
    assert runtime.VAULT_SCAN_CONCURRENCY == 2
    assert runtime.vault_scan_limiter.total_tokens == 2


async def _wait_until(predicate, *, timeout: float = 5.0, interval: float = 0.01) -> None:
    """Poll ``predicate`` cooperatively until it is true or ``timeout`` elapses."""
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError("condition was not met within the timeout")
        await anyio.sleep(interval)


async def test_health_stays_responsive_while_vault_scans_are_blocked(
    env: None, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from app import application as application_module
    from app.main import app

    # More than VAULT_SCAN_CONCURRENCY (2): some of these must queue on the
    # limiter, which is exactly the state /health must stay responsive
    # through — not just "one scan is running".
    scan_count = 3
    started = [threading.Event() for _ in range(scan_count)]
    release = threading.Event()
    index_lock = threading.Lock()
    next_index = {"n": 0}

    original_summarise = application_module.summarise_vault

    def blocking_summarise(*args, **kwargs):
        with index_lock:
            idx = next_index["n"]
            next_index["n"] += 1
        started[idx].set()
        release.wait(timeout=5)
        return original_summarise(*args, **kwargs)

    monkeypatch.setattr(application_module, "summarise_vault", blocking_summarise)

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        results: dict[str, httpx.Response] = {}

        async def run_summary(i: int) -> None:
            results[f"summary{i}"] = await client.get(
                "/api/v1/vault/summary", headers=auth_headers
            )

        async with anyio.create_task_group() as tg:
            for i in range(scan_count):
                tg.start_soon(run_summary, i)

            # At least VAULT_SCAN_CONCURRENCY of them must have actually
            # reached the blocking point — proving they hold limiter tokens,
            # not just that the tasks were scheduled.
            await _wait_until(lambda: sum(e.is_set() for e in started) >= 2)

            health_response = await client.get("/api/v1/health")
            assert health_response.status_code == 200

            release.set()

    assert next_index["n"] == scan_count
    for i in range(scan_count):
        assert results[f"summary{i}"].status_code == 200


async def test_search_and_summary_share_the_same_limiter(
    env: None, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two blocked search calls must fully occupy the limiter (capacity 2)
    and keep a concurrent summary call from starting at all — proving the
    two endpoints share one limiter rather than each getting their own.
    """
    from app import application as application_module
    from app.main import app

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

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        results: dict[str, httpx.Response] = {}

        async def run_search(i: int) -> None:
            results[f"search{i}"] = await client.get("/api/v1/search", headers=auth_headers)

        async def run_summary() -> None:
            results["summary"] = await client.get(
                "/api/v1/vault/summary", headers=auth_headers
            )

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
        assert results[key].status_code == 200


async def test_limiter_token_is_released_after_a_scan_raises(
    env: None, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A scan that raises must still give its limiter token back — otherwise
    every failure would permanently shrink the effective concurrency.
    """
    from app import application as application_module
    from app.main import app

    def failing_summarise(*args, **kwargs):
        raise RuntimeError("boom")

    original_summarise = application_module.summarise_vault
    monkeypatch.setattr(application_module, "summarise_vault", failing_summarise)

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        # VAULT_SCAN_CONCURRENCY calls that all fail — if any token leaked,
        # capacity would now be permanently reduced.
        for _ in range(2):
            response = await client.get("/api/v1/vault/summary", headers=auth_headers)
            assert response.status_code == 500

        # Restore only this one attribute — monkeypatch.undo() would also
        # revert the env fixture's env-var patches, since fixtures share one
        # MonkeyPatch instance per test.
        monkeypatch.setattr(application_module, "summarise_vault", original_summarise)

        # Bounded wait: if a token had leaked, this would hang past the
        # limiter's default (infinite) wait — the timeout on the client
        # request would surface it as a failure rather than a hang.
        with anyio.move_on_after(5) as scope:
            response = await client.get("/api/v1/vault/summary", headers=auth_headers)
        assert not scope.cancelled_caught, "a real request never got a limiter token back"
        assert response.status_code == 200


async def test_mcp_scan_waits_while_rest_holds_both_limiter_tokens(
    env: None, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two blocked REST searches must fully occupy the limiter (capacity 2)
    and keep a concurrent MCP search_notes call from starting at all —
    proving REST and MCP share one limiter rather than each getting their
    own. Fails before app/mcp_server.py's search_notes went through
    runtime.vault_scan_limiter, because the MCP call would instead run on
    anyio's separate default thread pool and start immediately.
    """
    from app import application as application_module
    from app.main import app

    started = [threading.Event() for _ in range(3)]
    release = threading.Event()
    next_index = {"n": 0}
    index_lock = threading.Lock()

    original_search = application_module.search_notes

    def blocking_search(*args, **kwargs):
        with index_lock:
            idx = next_index["n"]
            next_index["n"] += 1
        started[idx].set()
        release.wait(timeout=5)
        return original_search(*args, **kwargs)

    monkeypatch.setattr(application_module, "search_notes", blocking_search)

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        results: dict[str, object] = {}

        async def run_rest_search(i: int) -> None:
            results[f"rest{i}"] = await client.get("/api/v1/search", headers=auth_headers)

        async def run_mcp_search() -> None:
            results["mcp"] = await mcp.call_tool("search_notes", {})

        async with anyio.create_task_group() as tg:
            tg.start_soon(run_rest_search, 0)
            tg.start_soon(run_rest_search, 1)
            await _wait_until(lambda: started[0].is_set() and started[1].is_set())

            tg.start_soon(run_mcp_search)
            # Both limiter tokens are held by the two blocked REST searches,
            # so the MCP call must not be able to start yet.
            await anyio.sleep(0.2)
            assert not started[2].is_set(), (
                "MCP search started while both limiter tokens were held by REST"
            )

            release.set()

    assert started[2].is_set()
    assert results["rest0"].status_code == 200
    assert results["rest1"].status_code == 200
    assert results["mcp"].is_error is False


async def test_rest_scan_waits_while_mcp_holds_both_limiter_tokens(
    env: None, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reverse of the previous test: two blocked MCP search_notes calls
    must fully occupy the limiter and keep a concurrent REST /vault/summary
    request from starting — so neither transport is the privileged one.
    """
    from app import application as application_module
    from app.main import app

    started = [threading.Event(), threading.Event()]
    release = threading.Event()
    summary_started = threading.Event()
    next_index = {"n": 0}
    index_lock = threading.Lock()

    original_search = application_module.search_notes
    original_summarise = application_module.summarise_vault

    def blocking_search(*args, **kwargs):
        with index_lock:
            idx = next_index["n"]
            next_index["n"] += 1
        started[idx].set()
        release.wait(timeout=5)
        return original_search(*args, **kwargs)

    def blocking_summarise(*args, **kwargs):
        summary_started.set()
        return original_summarise(*args, **kwargs)

    monkeypatch.setattr(application_module, "search_notes", blocking_search)
    monkeypatch.setattr(application_module, "summarise_vault", blocking_summarise)

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        results: dict[str, object] = {}

        async def run_mcp_search(i: int) -> None:
            results[f"mcp{i}"] = await mcp.call_tool("search_notes", {})

        async def run_rest_summary() -> None:
            results["summary"] = await client.get(
                "/api/v1/vault/summary", headers=auth_headers
            )

        async with anyio.create_task_group() as tg:
            tg.start_soon(run_mcp_search, 0)
            tg.start_soon(run_mcp_search, 1)
            await _wait_until(lambda: started[0].is_set() and started[1].is_set())

            tg.start_soon(run_rest_summary)
            # Both limiter tokens are held by the two blocked MCP calls, so
            # the REST summary must not be able to start yet.
            await anyio.sleep(0.2)
            assert not summary_started.is_set(), (
                "REST summary started while both limiter tokens were held by MCP"
            )

            release.set()

    assert summary_started.is_set()
    assert results["mcp0"].is_error is False
    assert results["mcp1"].is_error is False
    assert results["summary"].status_code == 200


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
