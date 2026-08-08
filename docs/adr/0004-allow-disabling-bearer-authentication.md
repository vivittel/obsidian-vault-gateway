# ADR-0004: Allow disabling bearer authentication via AUTH_ENABLED

- Status: Accepted
- Date: 2026-08-05
- Decision owners: Repository owner
- Repository: `vivittel/obsidian-vault-gateway`
- Related documents:
  - [`docs/IMPLEMENTATION_PLAN.md`](../IMPLEMENTATION_PLAN.md) section 10
  - [`docs/MCP_IMPLEMENTATION_PLAN.md`](../MCP_IMPLEMENTATION_PLAN.md) section 8
  - [`README.md`](../../README.md) "Security invariants" and "Configuration"
  - `.env.example`

## Context

`docs/IMPLEMENTATION_PLAN.md` section 10 and `docs/MCP_IMPLEMENTATION_PLAN.md`
section 8 both specify Bearer authentication as unconditionally required on
every endpoint except `/api/v1/health`. That is correct for the deployment
this project was originally designed around (a container reachable only
through Caddy, itself reachable only over a private LAN or Tailscale
address), but it forecloses a legitimate alternative: a deployment where an
equivalent access-control boundary already exists outside the application
itself — for example a listener bound to `127.0.0.1`.

A loopback-only bind is not unconditionally sufficient on its own: it stops
remote network access, but it does not stop another user or a compromised
process on the same host, and it can be defeated by a proxy, a port-forward
rule, a sidecar, or a network namespace that exposes it despite the bind
address. For a deployment whose threat model trusts all same-host
principals, and where no such path has been introduced, application-level
Bearer authentication adds no additional remote-access control in that
specific case — it is not a blanket claim that loopback binding is always
sufficient. Requiring the application to also enforce Bearer there is, at
best, redundant friction; getting the threat-model assumption wrong is the
operator's responsibility, not something this application can verify from
inside a container.

Disabling authentication is not a plain feature flag: it removes a security
control, and getting the default or the scope wrong has real consequences.
This is recorded as an ADR, the same as ADR-0001/0002/0003, rather than
just a config addition with no design record.

## Decision

Bearer-token enforcement is enabled by default:

    AUTH_ENABLED=true

Disabling it requires the explicit setting:

    AUTH_ENABLED=false

The setting governs both REST and MCP. When disabled:

- protected REST endpoints do not validate or compare the parsed bearer
  credential (the Authorization header is still parsed by FastAPI's
  `HTTPBearer` security dependency before `require_token`'s body runs — see
  Consequences — but `require_token` itself performs no check);
- MCP requests are passed through *before* the Authorization header is
  read at all (`app/mcp_auth.py`'s `McpBearerAuthMiddleware.__call__`
  checks `settings.auth_enabled` first);
- `/api/v1/health` remains unauthenticated either way;
- `API_TOKEN` remains mandatory regardless, because it also signs
  pagination cursors (`app/services/cursor_service.py`);
- one `WARNING authentication_disabled` record is emitted during each
  gateway process startup (`app/main.py`'s `_lifespan`);
- the generated OpenAPI contract continues to advertise `bearerAuth` for
  every endpoint except `/api/v1/health`, independent of the runtime
  setting — the published contract does not vary per deployment.

Disabling bearer enforcement is permitted only when the deployment already
has an explicit external access-control boundary appropriate to its threat
model — a loopback-only listener, a firewall allowlist, a restrictive
reverse-proxy policy, or Tailscale ACLs/Grants that limit which identities
may reach the Gateway. Mere membership in a shared LAN or tailnet is not
sufficient on its own.

One setting intentionally governs both transports, so REST and MCP cannot
be configured into different enforcement states. REST and MCP remain
separate code paths, though (`app/auth.py`'s `require_token` vs.
`app/mcp_auth.py`'s `McpBearerAuthMiddleware`) — this is enforced by the one
shared setting, each side's own test coverage of it
(`tests/test_auth.py`'s `AUTH_ENABLED=false` cases for REST,
`tests/test_mcp_auth.py`'s for MCP), not by the two sharing one function
call or by a single cross-transport parity test.

## Consequences

### Positive

- Supports a legitimate deployment shape (an outer access-control boundary
  already present) without weakening the default for every other
  deployment.
- One toggle prevents operators from *configuring* REST and MCP into
  different enforcement states. It does not by itself prevent a future
  *implementation* bug from making the two behave asymmetrically — REST and
  MCP still check the setting through two separate code paths, each with
  its own test coverage, not one shared enforcement call.
- Explicit opt-in, plus a startup log line, keeps a disabled-auth
  deployment visible in `docker logs` rather than silent.

### Negative

- `openapi.json` / `GET /docs` shows `bearerAuth` as required even on a
  deployment that has disabled it. This is intentional (the published API
  contract is independent of any one deployment's runtime setting — see
  `README.md`'s REST reference), but it is a real, documented mismatch
  between the contract and one deployment's actual runtime behavior.
- An operator could set `AUTH_ENABLED=false` believing a shared LAN or
  Tailscale tailnet is itself sufficient, when it is not — mitigated only
  by documentation (`.env.example`, `README.md`, this ADR), not by any
  runtime check, since the application cannot verify its own network
  exposure from inside a container.
- REST's and MCP's mechanisms differ subtly even though the outcome is the
  same: REST's `HTTPBearer` security dependency still parses the
  Authorization header before `require_token` decides not to validate it,
  while MCP's middleware never reads the header at all when disabled.
  Both result in no enforcement. `app/auth.py`'s docstring previously
  described REST's case as "the Authorization header, if any, is not even
  inspected," which overstated it; fixed in a later documentation-only
  change to describe the actual FastAPI dependency-resolution order
  instead.

### Neutral

- No change to `API_TOKEN`'s own requirements (minimum length, stripping)
  or to cursor signing — both apply identically regardless of
  `AUTH_ENABLED`.

## Alternatives considered

### 1. Separate `REST_AUTH_ENABLED` / `MCP_AUTH_ENABLED` toggles

Rejected. No current supported deployment requires asymmetric
authentication between two transports reachable through the same network
boundary — REST and MCP are both bound to the same container, behind the
same Caddy instance or the same loopback listener. Two independent toggles
would double the states to reason about and test for no deployment that
actually needs them.

### 2. Auto-detect loopback-only binding instead of an explicit flag

Rejected. The application cannot reliably introspect its own network
exposure from inside a container — a loopback bind on the host side can
still be reached through a port-forwarding sidecar, a misconfigured
Compose network, or a reverse proxy the application has no visibility
into. Implicit behavior driven by inferred network topology is harder to
audit and harder to reason about than an explicit, logged setting.

## References

- [FastAPI: Security dependencies](https://fastapi.tiangolo.com/tutorial/security/)
- [Starlette: Requests](https://www.starlette.io/requests/)
