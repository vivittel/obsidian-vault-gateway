# ADR-0008: Normalize the bare `/mcp` path in-scope instead of redirecting

- Status: Accepted
- Date: 2026-08-08 (documents a decision already made in code during PR #1,
  2026-08-01 — see Context)
- Decision owners: Repository owner
- Repository: `vivittel/obsidian-vault-gateway`
- Related documents:
  - [`docs/MCP_IMPLEMENTATION_PLAN.md`](../MCP_IMPLEMENTATION_PLAN.md)
    "endpoint" section 8/9, sections 8 and 15 (auth-for-every-request and
    REST/MCP isolation requirements this decision has to keep true)
  - [`docs/adr/0001-switch-primary-interface-to-mcp.md`](0001-switch-primary-interface-to-mcp.md)
- GitHub PR #1 ("feat: add private Streamable HTTP MCP transport")

## Context

`docs/MCP_IMPLEMENTATION_PLAN.md`'s "endpoint" section originally specified,
and until this ADR still described as current, a fix for a routing gap
discovered while implementing PR #1: Starlette's `Mount` matches only
against the regex `{path}/{path:path}`, so `Mount("/mcp", app=...)`
structurally never matches the bare `/mcp` (no trailing slash) — confirmed
directly against `Mount.matches()`. The plan's first fix was a dedicated
`Route` that answered the bare path with an HTTP 307 redirect to `/mcp/`.

That redirect was replaced with the in-place ASGI scope rewrite this
repository has shipped since PR #1 itself (`app/main.py`'s
`_NormalizeBareMcpPath`) — before the plan document was ever corrected to
match. The redirect `Route` had two problems, both security-relevant:

1. **It sat outside `McpBearerAuthMiddleware`.** That middleware wraps the
   *mounted* MCP transport (`Mount(MCP_PREFIX, app=_mcp_app_with_auth)`);
   the redirect `Route` was a sibling of that `Mount`, not inside it. A
   request to the bare `/mcp` therefore received its 307 with **no bearer
   check at all** — section 8's "`/mcp`は`initialize`を含む全リクエストで認証
   必須" held for `/mcp/` but not for `/mcp` itself.
2. **It only handled `GET`/`POST`/`DELETE`.** Any other verb (`OPTIONS`,
   `PATCH`, `PUT`, ...) against the bare path fell through to the catch-all
   `Mount("/", app=rest_app)` and came back as a REST 404 envelope instead
   of ever reaching the MCP transport or its auth check.

Both gaps share one cause: a redirect is a second, independent route that
can drift out of sync with the thing it is supposed to be equivalent to.

## Decision

**Rewrite `scope["path"]`/`scope["raw_path"]` from `/mcp` to `/mcp/` before
Starlette's router ever sees the request, instead of responding with an
HTTP redirect.** `_NormalizeBareMcpPath` (`app/main.py`) is installed as
`Starlette(middleware=[Middleware(_NormalizeBareMcpPath)], ...)` — outside
both mounts, so it runs before routing decides which `Mount` a request goes
to at all:

```python
class _NormalizeBareMcpPath:
    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["path"] == MCP_PREFIX:
            normalized_path = f"{MCP_PREFIX}/"
            scope = {**scope, "path": normalized_path, "raw_path": normalized_path.encode("utf-8")}
        await self.app(scope, receive, send)
```

This makes `/mcp` byte-identical to `/mcp/` for every HTTP method, before
either mount is chosen — the router only ever sees the normalized path, so
there is no separate code path to fall out of sync with `Mount(MCP_PREFIX,
...)`'s own matching, and no unauthenticated response is possible: whichever
path arrives, the *same* `Mount` — and therefore the same
`McpBearerAuthMiddleware` — handles it.

## Consequences

### Positive

- No unauthenticated window for the bare `/mcp` path, for any HTTP method —
  closes both gaps above structurally, not by enumerating verbs or adding a
  second auth check to the redirect route.
- One fewer round trip for a client that requests the bare path (no 307 to
  follow), which is also what `README.md`'s smoke-test section now describes
  accurately.
- The fix lives in exactly one place (`_NormalizeBareMcpPath`) rather than
  being split across a `Route`'s handler and whatever auth logic would have
  had to be duplicated onto it.

### Negative

- A client that logs or inspects `scope["path"]` downstream sees the
  normalized `/mcp/`, never the `/mcp` it actually sent — an acceptable loss
  since nothing in this codebase's logging keys off that distinction (see
  `app/middleware.py`'s access log, which only ever runs for `/api/v1/*`).
- This is ASGI-scope surgery rather than a documented HTTP-level mechanism
  (like a redirect); a future contributor unfamiliar with Starlette's
  `Mount` regex could plausibly reintroduce a redirect-based "fix" for a
  similar gap elsewhere without realizing why this one specifically avoids
  it. Mitigated by `_NormalizeBareMcpPath`'s own docstring and this ADR.

## Alternatives considered

1. **Keep the 307-redirect `Route`, and move it inside
   `McpBearerAuthMiddleware`'s wrap so the auth check runs first.** Rejected:
   this still leaves the verb gap (a redirect `Route` registered for
   `GET`/`POST`/`DELETE` still cannot answer `OPTIONS`/`PATCH`/`PUT` without
   also enumerating those), and it still requires the two routes (mount
   match, redirect match) to be kept in agreement by hand as either changes.
2. **Register the `Mount` at both `/mcp` and `/mcp/`.** Rejected: mounting
   the same ASGI app twice under two prefixes is exactly the kind of
   duplication this ADR's "one cause" observation warns about, and it would
   have to be redone identically for any future mount this project adds.
3. **Do nothing — require clients to always call `/mcp/` with the trailing
   slash.** Rejected: several real MCP clients (this project's own smoke
   tests among them) address the bare `/mcp` path by default, and rejecting
   it outright would be a worse client-compatibility regression than either
   fix above.

## References

- `app/main.py` (`_NormalizeBareMcpPath`, `app` composition)
- `app/mcp_auth.py` (`McpBearerAuthMiddleware`)
- `docs/MCP_IMPLEMENTATION_PLAN.md` "endpoint" section
- `tests/test_mcp_protocol.py`, `tests/test_rest_regression.py`
