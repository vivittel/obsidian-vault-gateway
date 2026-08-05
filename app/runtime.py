"""Process-wide runtime state shared by both transports.

``search`` and ``get_vault_summary`` walk and read the whole vault — on a
large, Japanese-heavy vault, seconds of CPU-bound work (NFKC folding, YAML
parsing), all of it holding the GIL. Both run in a worker thread (REST:
app/routers/search.py, app/routers/vault.py; MCP: app/mcp_server.py) so they
no longer block the event loop, but sharing anyio's default limiter with
every other synchronous handler would let dozens of these run
"concurrently" for no throughput gain — just GIL thrashing and multiplied
peak memory — while also starving that pool for /health and everything
else. A single limiter, referenced by both transports through this module
(not imported by name into each, which would let a future edit or
monkeypatch quietly point one transport at a different limiter), isolates
vault scans instead: passing limiter=... to anyio.to_thread.run_sync
replaces the default limiter for that call rather than adding to it, so a
vault scan waiting on VAULT_SCAN_CONCURRENCY never consumes one of the
default pool's tokens.

A fixed constant, not a Settings field: this has no deployment-specific
value to tune, only a test-specific one (tests monkeypatch
``runtime.vault_scan_limiter.total_tokens`` or the object itself directly).

Per gateway *process*: running uvicorn with multiple workers would give
VAULT_SCAN_CONCURRENCY scans per worker, not in total. The current
Dockerfile runs a single worker, so this holds today.
"""

from __future__ import annotations

import anyio

VAULT_SCAN_CONCURRENCY = 2

vault_scan_limiter = anyio.CapacityLimiter(VAULT_SCAN_CONCURRENCY)
