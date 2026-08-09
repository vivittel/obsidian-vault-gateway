# ADR-0010: Reduce the REST surface to health-only

- Status: Accepted
- Date: 2026-08-09
- Decision owners: Repository owner
- Repository: `vivittel/obsidian-vault-gateway`
- Related documents:
  - [`docs/adr/0001-switch-primary-interface-to-mcp.md`](0001-switch-primary-interface-to-mcp.md)
    — switched MCP to the primary interface while keeping REST as a
    secondary one; this ADR is the conclusion of that direction, not a
    reversal of it
  - [`docs/adr/0005-single-structured-entry-point-for-chat-exports.md`](0005-single-structured-entry-point-for-chat-exports.md)
    — "a single structured entry point for chat exports" is now true
    without qualification: there is no second, REST-only raw-content path
  - `app/main.py`, `app/auth.py`, `app/middleware.py`, `app/models.py`,
    `app/application.py`
  - `tests/test_{search,vault,notes,inbox}.py` (migrated to
    `GatewayApplication`), `tests/test_openapi.py`

## Context

MCP has been the primary interface since ADR-0001. REST survived alongside
it as eight endpoints — `search`, `notes`, `vault/tree`, `vault/summary`,
`inbox/duplicate-candidates`, `inbox/notes` (create), `inbox/notes/append`,
and `health` — justified as "health checks, curl-based diagnostics, and
regression tests." In practice, per `Usage.md`'s own admission, the
developer does not use the REST API in day-to-day operation; every real
workflow goes through MCP. A REST route that exists but is never exercised
in production carries the same attack surface, documentation burden, and
test-maintenance cost as one that is, without any of the benefit.

This change reduces REST to `GET /api/v1/health` — kept because
Dockerfile's `HEALTHCHECK`, `compose.yaml`, and the example Caddy site block
all depend on it, and because it needs no authentication or business logic
of its own. Every other REST route is deleted, along with the
transport-specific plumbing (`InboxNoteCreateRequest`/
`InboxNoteAppendRequest`, `require_token`/`bearer_scheme`,
`RequestSizeLimitMiddleware`) that only existed to serve it.

`GatewayApplication` (`app/application.py`) is unaffected: every deleted
router was a thin adapter over it, and MCP's 8 tools call the exact same
methods they always did. The bulk of this change is therefore test
migration — moving coverage that was only reachable through REST's
`TestClient` onto `GatewayApplication` directly — not a behavioural change
to the application or service layers.

## Decision

1. **Every REST route except `GET /api/v1/health` is deleted.** MCP is the
   sole functional interface (the conclusion ADR-0001 pointed toward).
   `app/routers/{search,notes,vault,inbox}.py` are removed; `app/routers/
   health.py` is unchanged.

2. **FastAPI's `/docs`, `/redoc`, and `/openapi.json` remain in the
   application**, unauthenticated, exactly as before — this change does not
   set `docs_url=None` et al. What changes is the *content* they describe
   (one operation instead of eight) and, in the example deployment, their
   *reachability*: the Caddyfile (`docs/caddy/obsidian-api.Caddyfile`) now
   proxies only `/mcp` and `/api/v1/health`, so these three stay unreachable
   through that specific reverse proxy, exactly as the REST surface being
   larger did not make them reachable there before. A deployment that
   proxies more of the app than that example does would still reach them.
   Disabling them outright (`docs_url=None`) was considered and rejected: it
   would remove a still-useful diagnostic tool for the one route that
   remains, for a marginal reduction in surface the Caddyfile already
   achieves in the deployment that matters.

3. **Raw-Markdown note creation (`content`/`frontmatter`) is no longer
   reachable from any transport.** `InboxNoteCreateRequest` is deleted along
   with the REST route that was its only caller. This makes ADR-0005's
   "single structured entry point for chat exports" unconditionally true —
   previously it held only for MCP, while REST kept a second, raw-content
   path alive for existing callers. `GatewayApplication.create_inbox_note`
   and `inbox_service.create_inbox_note` are **not** deleted: they remain
   the internal primitive `create_chat_export_note` calls to actually write
   a note, and are tested directly (`tests/test_inbox.py`) rather than
   through a transport that no longer exposes them. `append_inbox_note`
   keeps working, unchanged, as an MCP tool.

4. **`RequestSizeLimitMiddleware` is deleted.** No remaining REST route
   accepts a body (`GET /api/v1/health` takes none), so the middleware that
   enforced `MAX_REQUEST_BYTES` pre-parse for REST is dead code. `/mcp`'s own
   body cap is unaffected: the MCP SDK's `RequestBodyLimitMiddleware`
   (wired in `app/mcp_server.py`'s `build_mcp_transport`) enforces
   `MAX_REQUEST_BYTES` independently, and always has. **A body-bearing REST
   route added in the future must re-introduce an equivalent cap** — this is
   not automatic, and is the one concrete regression risk this decision
   creates.

5. **`app/main.py`'s four exception handlers
   (`handle_gateway_error`/`handle_http_exception`/
   `handle_validation_error`/`handle_unexpected_error`) are all kept**, even
   though `handle_validation_error` is currently unreachable from any real
   route (`GET /api/v1/health` takes no parameters to fail validation on).
   They are the last line of defence for the documented contract that every
   failing REST response uses the `{"error": {...}}` envelope, never
   FastAPI's default `{"detail": ...}` shape — removing the currently-dead
   one would mean a future REST route silently reverts to FastAPI's default
   shape the instant it raises a validation error, with nothing to catch
   the regression until it shipped. `tests/test_error_envelope.py` pins
   `handle_gateway_error`/`handle_validation_error` by invoking them
   directly rather than through a route, precisely because no current route
   exercises every path through them.

6. **Test coverage was migrated to `GatewayApplication` before any
   production code was deleted, not after.** `tests/test_{search,vault,
   notes,inbox}.py` were rewritten to call `GatewayApplication` directly
   while `app/routers/*.py` still existed and the full suite still passed —
   confirming the migrated tests exercised the same application/service-layer
   behaviour before the routers that used to be their only path to it were
   removed. This is **not** a claim that the migrated tests verify the exact
   same thing the REST tests did: two categories of REST-transport-specific
   guarantee have no application-layer equivalent and are gone for good —
   - FastAPI's own pre-handler parameter validation (e.g. `Query(ge=1,
     le=200)` on `/search`'s `limit`, converted to a 400 before the handler
     ever runs). The *value itself* is still checked: `GatewayApplication.
     search_notes` independently re-validates `1 <= limit <= 200` (U7, the
     same defence-in-depth MCP's tool schema also gets), so out-of-range
     input is still rejected — only the earlier, transport-level rejection
     point and its distinct 400-with-envelope shape are gone.
   - The router-level `anyio.to_thread.run_sync(..., limiter=runtime.
     vault_scan_limiter)` offload for full-vault scans, which lived in
     `app/routers/search.py`/`vault.py`/`inbox.py`, not in
     `GatewayApplication`. MCP's own scanning tools (`search_notes`,
     `get_vault_summary`, `find_duplicate_candidates`) still go through the
     same limiter, and `tests/test_vault_scan_concurrency.py` still proves
     that isolation — just for MCP alone now, since there is no second
     transport left to share the limiter with.

7. **`AUTH_ENABLED` now gates only `/mcp`.** `GET /api/v1/health` has never
   required a bearer token, with or without `AUTH_ENABLED`; before this
   change the setting also gated REST's other seven routes, which are now
   gone. `app/config.py`'s `auth_enabled`/`api_token` field descriptions and
   `app/main.py`'s `authentication_disabled` startup warning are updated to
   say "MCP", not "REST or MCP", so the setting's actual scope stays
   accurately documented as the surface it governs shrinks.

8. **An unmatched REST path still returns `NOTE_NOT_FOUND`, not a
   transport-generic "route not found" code.** `app/main.py`'s
   `handle_http_exception` maps every `StarletteHTTPException` with
   `status_code == 404` to `ErrorCode.NOTE_NOT_FOUND` — a decision made when
   REST had several routes and "the thing that reads a note wasn't found"
   was still a plausible fit for "this path doesn't exist." With only
   `/api/v1/health` left, `GET /api/v1/search` now returns `NOTE_NOT_FOUND`
   for a reason that has nothing to do with notes at all — the route simply
   does not exist. This is acknowledged as semantically awkward but is
   **existing behaviour, not something this change alters**, and
   introducing a dedicated route-not-found `ErrorCode` is explicitly out of
   scope here: it would touch the published error vocabulary for a REST
   surface that is now one unauthenticated route, which is not worth the
   churn. `tests/test_error_envelope.py::test_404_unmatched_route_still_
   uses_the_envelope` pins the existing `NOTE_NOT_FOUND` mapping rather than
   inventing a new expectation.

9. **`AccessLogMiddleware` drops the `note_path`/`result_count`/
   `query_length` scope-state plumbing.** No remaining route ever sets
   `request.state.accessed_note`/`created_note`/`appended_note`/
   `result_count`, so `app/middleware.py` no longer initialises or reads
   them. `app/logging_config.py`'s `note_path`/`note` allow-list entry is
   kept (a future write-side log line could still use it, and it costs
   nothing to leave in place), but its docstring no longer cites a live
   caller — MCP's own U1 has always kept note paths out of its logs
   entirely, and REST no longer has a route that could set one either.
   `query_length` is not left dead the same way: IMPLEMENTATION_PLAN
   section 14 lists "検索語の長さ" (the search term's length) as a required
   field regardless of transport, so `app/mcp_server.py`'s `search_notes`
   tool now sets it on `_McpCall` (rendered as `q_len=...`, before
   `result_count`'s `results=...`) — the one field this decision *moves* to
   MCP rather than only removing from REST.

10. **`tests/test_openapi.py`'s `test_every_reachable_error_code_appears_
    on_some_operation` drift guard is replaced, not weakened in place.**
    That guard asserted every reachable `ErrorCode` appears in some
    operation's documented responses — a health-only REST surface cannot
    satisfy it (health only ever documents `INTERNAL_ERROR`). Its
    replacement, `test_rest_surface_is_exactly_health`, asserts the surface
    itself: `set(schema["paths"]) == {"/api/v1/health"}` and no
    `securitySchemes` entry. This is a different guarantee — "the surface
    hasn't silently grown back" rather than "every error code is
    documented somewhere" — and the latter is not replaced pound-for-pound:
    `ErrorCode` coverage for the codes REST no longer raises is left to
    `tests/test_mcp_tools.py`'s error-conversion coverage of `_McpCall`
    instead, which was already exhaustive for MCP's own error mapping.
    Likewise, `openapi.json`'s `components.schemas` is not pinned to an
    exact set (`HealthResponse`/`ErrorResponse`/`ErrorDetail`/`ErrorCode`
    today) — pydantic/FastAPI's schema-generation shape is not the contract
    worth protecting; the path-set and the absence of the bearer security
    scheme are.

11. **Rejected alternative: unregister the routers but keep the code.**
    Removing only the `include_router(...)` calls in `app/main.py` while
    leaving `app/routers/{search,notes,vault,inbox}.py`,
    `InboxNoteCreateRequest`/`InboxNoteAppendRequest`, and
    `RequestSizeLimitMiddleware` in place was considered. Rejected: it
    leaves genuinely dead code in the tree indefinitely (nothing imports or
    tests it, so it silently rots), and `openapi.json`/`tests/test_openapi.py`
    still need the exact same update either way — the smaller diff this
    alternative offers is illusory once those two are accounted for.

## Consequences

Positive:

- The REST attack surface (unauthenticated routes, request parsing,
  body-size handling) shrinks to one route with no body and no auth
  requirement.
- `openapi.json` shrinks from ~73 KB (eight operations, `bearerAuth`
  security scheme, `InboxNoteCreateRequest`/`InboxNoteAppendRequest` and
  every `ChatExport` sub-schema they pulled in) to four small schemas behind
  one operation.
- `app/routers/*.py`, `InboxNoteCreateRequest`/`InboxNoteAppendRequest`,
  `require_token`/`bearer_scheme`, and `RequestSizeLimitMiddleware` — all
  REST-only code with no MCP counterpart — are gone rather than merely
  unregistered.
- Test coverage for `app/services/vault_service.py`,
  `app/services/search_service.py`, and `app/services/note_service.py`
  moved to directly exercise `GatewayApplication`/the service functions
  rather than routing through REST's `TestClient`, which is a strictly more
  direct way to test them (no HTTP/ASGI layer between the test and the
  behaviour under test) and was already how MCP's own test coverage worked.

Negative / accepted:

- Raw-Markdown note creation (`content`/`frontmatter` at any transport
  boundary) is gone. Any external caller depending on
  `POST /api/v1/inbox/notes`'s `content` field breaks. This repository's own
  usage was already MCP-only (`Usage.md`'s prior "REST API の位置づけ"
  section already said as much), so this is treated as a real but
  low-probability break, not a design flaw.
- `InboxNoteAppendRequest`'s `_MAX_CONTENT_CHARS` (2,000,000) cap on append
  content is gone with it. This does not weaken the effective bound: MCP's
  `append_inbox_note(path, content)` never had a request-shape cap of its
  own, and the two limits that actually bound it —
  `MAX_REQUEST_BYTES` (2 MiB, enforced by the MCP SDK's own middleware) and
  `MAX_NOTE_SIZE_BYTES` (1 MiB, `inbox_service`'s pre-write check) — are
  both unchanged and were always the effective bound for MCP callers.
  Whether `append_inbox_note`'s signature should gain an explicit
  application-layer cap of its own is left for a future change.
- A body-bearing REST route reintroduced later must independently
  reintroduce a request-size cap (decision 4) and its own
  `require_token`-equivalent dependency (decision 3's plumbing was deleted,
  not merely disabled) — neither comes back automatically.
- The `ErrorCode` exhaustiveness guard REST's OpenAPI schema used to provide
  is gone in its prior form (decision 10); coverage for those codes now
  lives entirely in `tests/test_mcp_tools.py`, which was already
  comprehensive but was not previously the *only* thing providing that
  guarantee.

## Alternatives considered

- **Unregister routers, keep the code** (decision 11) — rejected: leaves
  dead code and does not avoid the `openapi.json`/test updates either way.
- **Disable `/docs`/`/redoc`/`/openapi.json` outright** (decision 2) —
  rejected: loses a still-useful diagnostic tool for a marginal surface
  reduction the example Caddyfile already achieves for the deployment that
  matters; can be revisited independently if the attack-surface concern
  ever outweighs the diagnostic value.
- **Move raw-content creation onto an MCP tool argument** instead of
  deleting it — rejected: it would change MCP's `create_inbox_note` tool
  signature and its `_CREATE_INBOX_NOTE_ALLOWED_ARGUMENTS`/schema/tool
  description for a capability nothing in this repository's own usage
  exercises, and would work against ADR-0005's "single structured entry
  point" direction rather than completing it.
- **Introduce a dedicated route-not-found `ErrorCode`** (decision 8) —
  rejected as out of scope for a REST surface reduced to one
  unauthenticated route; revisit if REST ever grows a second route.

## References

- `app/main.py`, `app/auth.py`, `app/middleware.py`, `app/models.py`,
  `app/application.py`, `app/config.py`, `app/logging_config.py`,
  `app/runtime.py`, `app/mcp_auth.py`, `app/mcp_server.py`
- `tests/test_openapi.py`, `tests/test_error_envelope.py`,
  `tests/test_{search,vault,notes,inbox,auth,middleware,logging,log_format,
  vault_scan_concurrency,rest_regression}.py`
- `openapi.json`, `docs/caddy/obsidian-api.Caddyfile`
- `docs/adr/0001-switch-primary-interface-to-mcp.md`,
  `docs/adr/0005-single-structured-entry-point-for-chat-exports.md`
