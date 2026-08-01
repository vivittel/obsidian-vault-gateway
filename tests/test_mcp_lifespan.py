"""MCP session manager lifecycle — MCP_IMPLEMENTATION_PLAN section 18 (U8), V11.

Tests the raw one-shot lifecycle mechanics of ``MCPServer.session_manager`` /
``streamable_http_app()`` directly, against throwaway ``MCPServer``
instances — never the shared ``app.mcp_server.mcp`` singleton, whose
``session_manager.run()`` is entered exactly once for the whole test session
by conftest.py's ``mcp_client`` fixture (see its docstring for why: ``.run()``
can only be called once per instance, ever).
"""

from __future__ import annotations

import pytest
from mcp.server.mcpserver import MCPServer

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _build_streamable_http_app(mcp_server: MCPServer):
    return mcp_server.streamable_http_app(
        streamable_http_path="/", json_response=True, stateless_http=True
    )


def test_session_manager_raises_before_streamable_http_app_is_called() -> None:
    fresh_mcp = MCPServer(name="lifespan-test")
    with pytest.raises(RuntimeError):
        _ = fresh_mcp.session_manager


def test_session_manager_is_usable_after_streamable_http_app_is_called() -> None:
    fresh_mcp = MCPServer(name="lifespan-test")
    _build_streamable_http_app(fresh_mcp)
    # No longer raises — the property now resolves to a real instance.
    assert fresh_mcp.session_manager is not None


async def test_session_manager_run_works_inside_lifespan() -> None:
    fresh_mcp = MCPServer(name="lifespan-test")
    _build_streamable_http_app(fresh_mcp)

    async with fresh_mcp.session_manager.run():
        assert fresh_mcp.session_manager is not None


async def test_session_manager_run_cannot_be_reentered_after_it_closes() -> None:
    """The one-shot-forever guarantee is the observable proof of a clean
    close: if the session manager's task group and internal state were not
    torn down properly on exit, nothing would stop a second ``.run()`` from
    silently reusing (or corrupting) leftover state. Instead it always
    raises, whether the previous ``.run()`` is still active or has already
    exited.
    """
    fresh_mcp = MCPServer(name="lifespan-test")
    _build_streamable_http_app(fresh_mcp)

    async with fresh_mcp.session_manager.run():
        pass

    with pytest.raises(RuntimeError, match="can only be called once"):
        async with fresh_mcp.session_manager.run():
            pass


async def test_session_manager_run_rejects_concurrent_reentry_too() -> None:
    fresh_mcp = MCPServer(name="lifespan-test")
    _build_streamable_http_app(fresh_mcp)

    async with fresh_mcp.session_manager.run():
        with pytest.raises(RuntimeError, match="can only be called once"):
            async with fresh_mcp.session_manager.run():
                pass


def test_streamable_http_app_called_again_repoints_session_manager() -> None:
    """Documents the hazard build_mcp_transport()'s docstring warns about:
    calling ``streamable_http_app()`` a second time on the *same* instance
    silently creates a fresh, never-started session manager and repoints
    ``.session_manager`` at it — orphaning whatever ASGI app was returned by
    the first call, which still holds the original instance via its own
    closure. This is exactly why app/main.py and this test file each use
    their own ``MCPServer`` instance rather than sharing one.
    """
    fresh_mcp = MCPServer(name="lifespan-test")
    _build_streamable_http_app(fresh_mcp)
    first_session_manager = fresh_mcp.session_manager

    _build_streamable_http_app(fresh_mcp)
    second_session_manager = fresh_mcp.session_manager

    assert first_session_manager is not second_session_manager
