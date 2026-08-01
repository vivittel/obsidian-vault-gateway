# ADR-0002: Use MCP Python SDK v2, not the v1.x line, for Phase 1.5

- Status: Accepted
- Date: 2026-07-31
- Decision owners: Repository owner
- Repository: `vivittel/obsidian-vault-gateway`
- Related documents:
  - [`docs/adr/0001-switch-primary-interface-to-mcp.md`](0001-switch-primary-interface-to-mcp.md)
  - [`docs/MCP_IMPLEMENTATION_PLAN.md`](../MCP_IMPLEMENTATION_PLAN.md)

## Context

`MCP_IMPLEMENTATION_PLAN.md` section 5 (written 2026-07-31, alongside
ADR-0001) specified pinning the MCP Python SDK's stable v1 line (candidate:
`mcp==1.28.1`), explicitly avoiding v2 as a pre-release — while also
instructing that PyPI's actual latest stable release be checked at
implementation time, before pinning.

That check, performed at the start of Phase 1.5 implementation, found the
opposite of what section 5 assumed:

- `mcp` 2.0.0 was published to PyPI on 2026-07-28 as the current **stable**
  release line. `pip install mcp` installs 2.x by default.
- v1.x (1.28.1, and a newer 1.29.0 also since released) moved to a
  maintenance-only line: critical bug fixes and security patches only, no
  new features.
- Staying on v1.x now requires an explicit upper bound (`mcp>=1.28,<2`); an
  unpinned resolve lands on 2.x.

Section 9's concept code (`from mcp.server.fastmcp import FastMCP`,
transport options passed to the `FastMCP` constructor) targets the v1.x API
shape. Verified directly against the installed 2.0.0 package, several of
these have moved or changed shape:

- `FastMCP` → `mcp.server.mcpserver.MCPServer`.
- Transport options (`stateless_http`, `json_response`,
  `streamable_http_path`, `max_request_body_size`, `transport_security`)
  moved off the constructor onto `MCPServer.streamable_http_app(...)`.
- `mcp.types` — the SDK's own recommended import spelling for
  `ToolAnnotations` and friends — is now a wildcard mirror of a separate
  `mcp_types` package, not defined in-tree.
- A tool that raises anything other than `mcp.shared.exceptions.MCPError`
  has `str(exc)` embedded into the client-visible response by the SDK's own
  fallback handling (`app/mcp_server.py`'s `_McpCall` exists specifically
  because of this).
- `server/discover` (the 2026-07-28 spec's replacement for the legacy
  `initialize` handshake) requires header/envelope fields
  (`MCP-Protocol-Version`, `Mcp-Method`, `Mcp-Name`, `params._meta`) not
  documented anywhere in this repository's planning docs — found only by
  reading the installed SDK source and testing requests against it directly.

## Decision

Phase 1.5 pins `mcp==2.0.0`, not the v1.x line `MCP_IMPLEMENTATION_PLAN.md`
section 5 originally specified.

This follows section 5's own instruction — check PyPI's actual latest stable
release before pinning — applied honestly: by the time implementation
started, 2.0.0 was that release and v1.x was the deprecated line, the
reverse of what section 5 assumed when it was written.

## Consequences

### Positive

- Builds on the SDK's currently maintained, feature-receiving line rather
  than one already limited to security/bug patches.
- Avoids a near-term forced migration: staying on 1.28.1/1.29.0 would only
  postpone, not avoid, moving to 2.x.
- The 2026-07-28 spec's `server/discover` flow, and any future SDK
  improvements, are only available on 2.x.

### Negative

- `app/mcp_server.py` and `app/main.py` are written against v2's API shape
  (`MCPServer`, `streamable_http_app()`'s argument placement,
  `mcp.shared.exceptions.MCPError`), which is not backward-compatible with
  v1.x — reverting to v1.x would mean rewriting both files, not just
  changing a version pin.
- The modern (`server/discover`) wire protocol's header/envelope
  requirements are undocumented outside the SDK's own source; a future SDK
  release could change them without a corresponding upstream documentation
  update.
- The dependency surface grew beyond what section 5 anticipated for a v1.x
  pin: `httpx2` (a separate major-version fork of `httpx` the SDK depends on
  directly — distinct from this project's own `httpx` dev dependency),
  `mcp-types` (exact-pinned to the SDK version), `opentelemetry-api`,
  `pyjwt[crypto]` (pulling in `cryptography`), `jsonschema`, `sse-starlette`,
  `python-multipart`.

### Neutral

- `starlette>=0.27` (the SDK's lower bound, no upper bound) coexists without
  conflict with this project's pinned `starlette==1.3.1` — verified by
  installing `mcp==2.0.0` into the existing environment and running the full
  REST test suite unchanged.
- ADR-0001's architecture (MCP as primary interface, REST as secondary, no
  internal HTTP calls between them) is unaffected by which SDK line
  implements it.

## Relationship to ADR-0001

ADR-0001's "Review conditions" lists "MCP Python SDK v2 requires a
significant architectural migration" as a trigger to revisit that decision.
This ADR records that v2 was adopted from the start of Phase 1.5 — there was
no v1-to-v2 migration inside this repository, since v1.x code was never
merged. ADR-0001's own decision (MCP as primary interface) is not reopened
by this; only the SDK line implementing it changed from what section 5
anticipated.

## Alternatives considered

### 1. Pin `mcp>=1.28,<2` as `MCP_IMPLEMENTATION_PLAN.md` section 5 originally specified

Rejected.

Reasons:

- v1.x is confirmed maintenance-only as of this decision; building new
  Phase 1.5 functionality on a line that will not receive further features
  contradicts section 5's own "最新安定版を確認する" instruction once that
  check was actually performed.
- Would require a second migration to v2 later, once v1.x's maintenance
  window narrows further, duplicating work already done here.

### 2. Wait for a v2.x point release beyond 2.0.0 before pinning

Rejected for Phase 1.5's timeline.

Reasons:

- 2.0.0 is already the designated stable release, not a pre-release; there
  is no signal it is unstable.
- Exact pinning (`mcp==2.0.0`, matching this project's existing
  dependency-pinning discipline for every other runtime dependency) means a
  later point release is a deliberate, reviewed upgrade, not an accidental
  one.

## References

- [MCP Python SDK](https://py.sdk.modelcontextprotocol.io/)
- [MCP Python SDK: Migration Guide](https://py.sdk.modelcontextprotocol.io/migration/)
- [PyPI: mcp](https://pypi.org/project/mcp/)
