"""app/main.py's dedicated vault-scan capacity limiter.

``search`` and ``get_vault_summary`` walk and read the whole vault; both run
in a worker thread through ``rest_app.state.vault_scan_limiter`` instead of
FastAPI's default thread pool, so a blocked or slow scan can never starve
``/health`` (or any other lightweight REST request) of a thread. Checking
that the two handlers are ``async def`` and the rest are plain ``def``
(the two ``test_*_functions`` checks below) proves the *shape* of the fix
but not the *isolation* it exists for — a limiter is easy to wire up wrong
(e.g. passed to the wrong call, or omitted) in a way that still leaves every
function sync/async as expected. Only a real concurrency test proves
isolation, which is why most of this file drives the app end-to-end through
``httpx.AsyncClient`` + ``ASGITransport`` rather than through ``TestClient``
(which runs the whole app on a separate thread via a portal, and would not
reliably reproduce the production event-loop-plus-worker-threads shape these
tests depend on).
"""

from __future__ import annotations

import inspect
import threading
import time

import anyio
import httpx
import pytest

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


def test_lightweight_endpoints_are_plain_sync_functions() -> None:
    # These share FastAPI's default thread pool (app/main.py's limiter is
    # only for the two vault-scanning handlers above) — a plain `def` is
    # enough for them; no dedicated limiter needed.
    assert not inspect.iscoroutinefunction(health.get_health)
    assert not inspect.iscoroutinefunction(notes.read_note)
    assert not inspect.iscoroutinefunction(vault.get_vault_tree)
    assert not inspect.iscoroutinefunction(inbox.create_note)
    assert not inspect.iscoroutinefunction(inbox.append_note)


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
