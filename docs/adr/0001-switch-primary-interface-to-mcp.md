# ADR-0001: Primary interface changed from GPT Actions to MCP

- Status: Accepted
- Date: 2026-07-31
- Decision owners: Repository owner
- Repository: `vivittel/obsidian-vault-gateway`
- Related documents:
  - [`docs/IMPLEMENTATION_PLAN.md`](../IMPLEMENTATION_PLAN.md)
  - [`docs/MCP_IMPLEMENTATION_PLAN.md`](../MCP_IMPLEMENTATION_PLAN.md)
  - [`docs/PHASE1_PLAN.md`](../PHASE1_PLAN.md)

## Context

The original implementation plan defined a REST API intended to be called from a Custom GPT through ChatGPT Actions.

The planned flow was:

```text
ChatGPT Custom GPT
        │
        │ HTTPS / GPT Actions
        ▼
Caddy
        │
        ▼
obsidian-api
        │
        ▼
Obsidian Vault
```

Phase 1 implemented and verified the secure REST backend:

- full-vault Markdown search
- note reading
- new-note creation restricted to `00_Inbox/ChatGPT`
- Bearer Token authentication
- path traversal rejection
- symlink rejection
- read-only whole-vault mount
- read-write Inbox mount
- non-root Docker execution
- LiveSync synchronization

During deployment, the actual primary requirement was clarified:

> Use the private Obsidian Vault from the ChatGPT desktop application.

The Gateway must not be exposed to the public internet.

ChatGPT Actions requires an endpoint that OpenAI-hosted services can reach. That conflicts with the private-network requirement unless a public ingress or tunnel is introduced.

The ChatGPT desktop application, Codex CLI, and Codex IDE extension can use MCP servers configured on the same Codex host. Streamable HTTP supports Bearer Token authentication and can connect to an address reachable from the local machine, including a private LAN or Tailscale address.

## Decision

MCP becomes the primary client interface for Obsidian Vault Gateway.

The implementation will add a Streamable HTTP MCP endpoint:

```text
https://obsidian-api.example.com/mcp
```

The endpoint remains reachable only through the private network.

The existing REST API remains supported as a secondary interface for:

- Docker health checks
- curl-based diagnostics
- regression tests
- troubleshooting
- non-MCP clients
- OpenAPI-based inspection

REST and MCP will use the same application and service layer.

The MCP adapter will not call the REST API over HTTP. Both transports will invoke shared in-process functions.

## Resulting architecture

```text
ChatGPT desktop app ─┐
Codex CLI            ├─ MCP / Streamable HTTP / Bearer
Codex IDE extension  ┘
           │
           ▼
Private Caddy endpoint
           │
           ▼
Obsidian Vault Gateway
├── MCP adapter
├── REST adapter
└── shared application/services
           │
           ▼
Obsidian Vault
├── whole Vault: read-only
└── 00_Inbox/ChatGPT: read-write
```

## Consequences

### Positive

- The Gateway does not need to be exposed to the public internet.
- The original ChatGPT desktop requirement is addressed directly.
- ChatGPT desktop, Codex CLI, and the IDE extension can share MCP configuration.
- Existing Phase 1 security work is reused.
- Existing REST tests remain useful.
- curl remains available for low-level troubleshooting.
- The same service functions back REST and MCP, reducing inconsistent behavior.
- Write tools can be marked separately from read-only tools.
- Codex can prompt only for write tools by using an approval policy such as `writes`.

### Negative

- MCP Python SDK becomes an additional dependency.
- ASGI lifespan management becomes more complex.
- Existing middleware must be checked for compatibility with Streamable HTTP.
- MCP protocol tests must be added.
- REST and MCP error models require separate adapters.
- Client behavior must be verified against ChatGPT desktop and Codex versions.
- MCP SDK major-version changes may require migration work.

### Neutral

- Caddy remains in use.
- The Docker container continues using port 8000 internally.
- No host port is published.
- The existing Bearer Token remains in use.
- Vault mount permissions do not change.
- LiveSync architecture does not change.
- CouchDB is not accessed directly.

## Alternatives considered

### 1. Continue with ChatGPT Actions and expose the REST API publicly

Rejected.

Reasons:

- Conflicts with the private-network requirement
- Makes the entire Vault read API internet-reachable
- Bearer Token compromise could expose all Markdown notes
- Requires additional ingress hardening, rate limiting, monitoring, and incident response
- Unnecessary for a desktop-local client

### 2. Use Tailscale Funnel

Rejected for the primary design.

Reasons:

- Funnel is public internet ingress
- Anyone can reach the Funnel URL
- Bearer Token becomes the main access barrier
- Compromise could expose Vault content
- The desktop MCP client can connect privately without Funnel

Funnel may be reconsidered only for a separate, reduced-scope Gateway.

### 3. Create a new MCP-only repository

Rejected.

Reasons:

- Duplicates path-security logic
- Duplicates search and note parsing
- Duplicates Inbox write logic
- Risks divergence between REST and MCP
- Discards tested Phase 1 work
- Increases deployment and maintenance cost

### 4. Finish all original REST phases before adding MCP

Rejected.

Reasons:

- MCP is now the primary interface
- Later service contracts should be designed for both transports
- Delaying MCP increases later refactoring
- Client integration risk should be tested before adding more features

### 5. Remove REST and replace it entirely with MCP

Rejected.

Reasons:

- REST health endpoint is already used by Docker
- curl is valuable for diagnostics
- REST regression tests isolate filesystem behavior from MCP protocol issues
- OpenAPI remains useful documentation
- Existing API is already deployed and functioning

### 6. Use a local STDIO bridge

Rejected as the main architecture.

Reasons:

- Requires a bridge installation on every PC
- Adds another process and configuration layer
- Duplicates networking and authentication concerns
- The OMV server is already always available over private HTTPS

STDIO remains a possible emergency diagnostic adapter, not the primary transport.

## Implementation implications

The next phase is Phase 1.5, not the original Phase 2.

Required work:

1. Record this architecture change.
2. Extract a transport-neutral application layer.
3. Extract common Bearer Token verification.
4. Add a pinned MCP Python SDK dependency.
5. Add four MCP tools:
   - `get_health`
   - `search_notes`
   - `read_note`
   - `create_inbox_note`
6. Mount Streamable HTTP at `/mcp`.
7. Add MCP authentication.
8. Add protocol and security tests.
9. Verify with MCP Inspector.
10. Verify with ChatGPT desktop.
11. Verify with Codex CLI.
12. Verify LiveSync after MCP write.

## Security invariants

This decision does not relax any existing security rule.

The following remain mandatory:

- whole Vault mount is read-only
- only `00_Inbox/ChatGPT` is writable
- no delete endpoint
- no move endpoint
- no rename endpoint
- no arbitrary-path write
- no `.obsidian` access
- no CouchDB access
- no shell execution
- no Git operation
- no absolute paths in responses
- no token or note content in logs
- no public internet exposure

## Status of the original plan

`docs/PHASE1_PLAN.md` remains the completed historical design for Phase 1.

`docs/IMPLEMENTATION_PLAN.md` is revised to make MCP the primary interface.

The original ChatGPT Actions phase is superseded. It may be reconsidered only as a separate public-integration project with a reduced data scope and a separate threat model.

## Review conditions

Revisit this decision if one of the following becomes true:

- ChatGPT Web gains direct support for the same private Codex-host MCP configuration
- Secure MCP Tunnel becomes necessary for the target client
- multiple remote users require access
- OAuth becomes preferable to a single Bearer Token
- the Gateway becomes a public service
- MCP Python SDK v2 requires a significant architectural migration
- a dedicated local agent replaces direct ChatGPT/Codex access

## References

- [OpenAI: Model Context Protocol](https://learn.chatgpt.com/docs/extend/mcp)
- [MCP Python SDK](https://py.sdk.modelcontextprotocol.io/)
- [MCP Python SDK: Building Servers](https://py.sdk.modelcontextprotocol.io/server/)

