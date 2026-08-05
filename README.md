# Obsidian Vault Gateway

A secure gateway over a private Obsidian vault: full-vault search, note
reads, staged directory/summary browsing, and note creation and append
restricted to one directory. **MCP is the primary interface** — for the
ChatGPT desktop app, Codex CLI, and the Codex IDE extension, all sharing one
MCP server configuration on the same Codex host, without exposing the
Gateway to the public internet. A secondary REST API is kept for health
checks, curl-based diagnostics, and regression tests.

This is **Phase 2** of `docs/IMPLEMENTATION_PLAN.md` — implemented, covered by
the automated test suite, and verified on the OMV host together with LiveSync,
Obsidian on a PC, and Obsidian on an iPhone. Only the container memory limit
and Docker log-rotation settings added afterwards remain to be checked against
the latest deployed image (see the checklist below). See
`docs/adr/0001-switch-primary-interface-to-mcp.md` for why MCP replaced the
original ChatGPT Actions plan, `docs/adr/0002-use-mcp-python-sdk-v2.md` for
why this runs on the MCP Python SDK's v2 line,
`docs/adr/0003-allow-os-replace-for-inbox-append.md` for why note append is
the one place `os.replace()` is used,
`docs/adr/0004-allow-disabling-bearer-authentication.md` for when and how
bearer authentication may be disabled, and `docs/MCP_IMPLEMENTATION_PLAN.md`
for the MCP design in full. Phase 1 and Phase 1.5 (the REST-only and
MCP-introduction predecessors) are documented as completed history in
`docs/PHASE1_PLAN.md` and `docs/IMPLEMENTATION_PLAN.md`.

## Security invariants

These hold regardless of what future phases add (see `AGENTS.md`), for both
transports:

- The whole vault is mounted **read-only**.
- Only `00_Inbox/ChatGPT` is writable, and only through the `create_inbox_note`
  / `append_inbox_note` MCP tools or `POST /api/v1/inbox/notes` /
  `POST /api/v1/inbox/notes/append`. `append_inbox_note` can only extend an
  existing note already directly inside that directory — it cannot create
  one, and cannot target a subdirectory or anywhere else.
- There is no delete, move, rename, or arbitrary-path write endpoint or tool.
- Every path is validated against traversal, absolute paths, hidden files, and
  symlinks before touching the filesystem (`app/services/path_security.py`).
- Responses and logs never contain an absolute host path, a bearer token, or
  note content — only vault-relative paths.
- The Gateway is not exposed to the public internet — reachable only over a
  private LAN or Tailscale address, behind Caddy.
- Bearer authentication (`API_TOKEN`) is required by default (`AUTH_ENABLED=true`).
  It may only be disabled (`AUTH_ENABLED=false`) when an equivalent
  access-control boundary already exists outside the application, e.g. a
  localhost-only listener. Being on a private LAN or Tailscale tailnet is
  **not** by itself such a boundary — anyone else with access to that network
  or tailnet would reach every endpoint unauthenticated. `API_TOKEN` itself
  stays required either way: it also signs pagination cursors. See
  `docs/adr/0004-allow-disabling-bearer-authentication.md` for the decision
  and accepted boundary conditions.

MCP and REST call the same `app/application.py` and service functions
(`app/services/`); neither transport calls the other over HTTP, so they can
never diverge in behaviour for the same operation.

## MCP (primary interface)

Endpoint: `/mcp`, Streamable HTTP transport, Bearer authentication by default
(same `API_TOKEN` as REST; runtime-disableable with `AUTH_ENABLED=false`, see
Security invariants above and Configuration below). Stateless
(`stateless_http=True`): no session is
tracked across requests, so there is nothing for a client to terminate and
`DELETE /mcp` answers **405** — the status the spec prescribes for a server
that does not support session termination (asserted in
`tests/test_mcp_protocol.py`). Clients handle it; it is not an error condition.

### Tools

| Tool | Type | Approval |
|---|---|---|
| `get_health` | read | auto |
| `search_notes` | read | auto |
| `read_note` | read | auto |
| `get_vault_tree` | read | auto |
| `get_vault_summary` | read | auto |
| `create_inbox_note` | write | **prompt** |
| `append_inbox_note` | write | **prompt** |

`search_notes` before `read_note` when the exact path is unknown — search
results' `path` can be passed directly to `read_note`, `get_vault_tree`'s
`path`, or `append_inbox_note`'s `path`. `get_vault_tree` lists one folder's
direct children at a time (folders before notes); `get_vault_summary` gives
vault-wide counts, sizes, and top tags without exposing any note body or
title. Both support cursor-based pagination — pass a non-null `next_cursor`
back as `cursor` with the same other arguments to fetch the next page; a
cursor is only valid for the exact arguments it was minted with, and
becomes invalid if `API_TOKEN` is rotated. `create_inbox_note` always writes
a new file under `00_Inbox/ChatGPT`; `append_inbox_note` appends to an
existing file already directly inside it. Neither can overwrite, delete,
move, or rename notes, and `create_inbox_note` does not accept a path from
the caller.

**Write approval is not left to `ToolAnnotations` alone.** Both
`create_inbox_note` and `append_inbox_note` are annotated
`readOnlyHint: false`, which is a signal a client's own policy can choose to
ignore — the Codex configuration below sets an explicit approval policy
(`default_tools_approval_mode`, plus a per-tool override for each) so a
write actually prompts for confirmation rather than depending on the client
interpreting the annotation the way this Gateway intends.

### ChatGPT desktop app

```text
Settings → MCP servers → Add server
  Name:            Obsidian Vault
  Type:            Streamable HTTP
  URL:             https://obsidian-api.example.com/mcp
  Authentication:  Bearer token
```

Restart the app after saving. Confirm the connection in the composer with
`/mcp`. Read tools (`get_health`, `search_notes`, `read_note`,
`get_vault_tree`, `get_vault_summary`) run without confirmation;
`create_inbox_note` and `append_inbox_note` prompt before writing.

### Codex CLI / Codex IDE extension

Both share the same Codex host configuration
(`~/.codex/config.toml`):

```toml
[mcp_servers.obsidian_vault]
url = "https://obsidian-api.example.com/mcp"
bearer_token_env_var = "OBSIDIAN_VAULT_MCP_TOKEN"
default_tools_approval_mode = "writes"
startup_timeout_sec = 20
tool_timeout_sec = 60
enabled = true
required = true

[mcp_servers.obsidian_vault.tools.create_inbox_note]
approval_mode = "prompt"

[mcp_servers.obsidian_vault.tools.append_inbox_note]
approval_mode = "prompt"
```

```bash
export OBSIDIAN_VAULT_MCP_TOKEN='...'   # never write the token into config.toml
```

Verify:

```bash
codex mcp list
```

or, inside the Codex TUI, `/mcp`. A prompt like:

> Obsidian VaultからSelf-hosted LiveSync CLIに関するノートを検索し、
> 最も関連するノートを読んで要約してください。

should run `search_notes`/`read_note` without a prompt. A save request:

> この検証結果を「MCP接続テスト」というタイトルでObsidian Inboxへ保存してください。

should prompt for approval before `create_inbox_note` runs, and Codex should
only report the note as saved after the tool call actually returns success.

### MCP Inspector

```bash
npx -y @modelcontextprotocol/inspector
```

Connect to `https://obsidian-api.example.com/mcp` (Streamable HTTP),
set the Bearer token, and confirm all seven tools are listed and callable.

### Configuration

`MCP_ALLOWED_HOSTS` (required) is a comma-separated Host-header allowlist for
the transport's DNS-rebinding protection. Without it, the SDK defaults to
allowing only `localhost`/`127.0.0.1`, which silently rejects every request
Caddy forwards with a real Host header — set it to whatever hostname(s) the
Gateway is actually reached by, e.g. `obsidian-api.example.com`. See
`.env.example`.

## REST (secondary interface)

Kept for `docker healthcheck`, curl-based diagnostics, regression tests, and
any non-MCP client.

| Method | Path | operationId | Auth |
|---|---|---|---|
| GET | `/api/v1/health` | `getHealth` | none |
| GET | `/api/v1/search` | `searchNotes` | Bearer |
| GET | `/api/v1/notes` | `readNote` | Bearer |
| GET | `/api/v1/vault/tree` | `getVaultTree` | Bearer |
| GET | `/api/v1/vault/summary` | `getVaultSummary` | Bearer |
| POST | `/api/v1/inbox/notes` | `createInboxNote` | Bearer |
| POST | `/api/v1/inbox/notes/append` | `appendInboxNote` | Bearer |

"Bearer" above reflects the default (`AUTH_ENABLED=true`); with
`AUTH_ENABLED=false` these endpoints accept requests with no Authorization
header at all. `openapi.json` always advertises Bearer authentication for
them regardless of `AUTH_ENABLED` — the published API contract is
intentionally independent of any one deployment's runtime setting.

Full schema: `openapi.json` (regenerate with `scripts/export_openapi.py`
after changing any router/model), or `GET /docs` on a running instance.

`GET /api/v1/notes` takes the note path as a **query parameter**
(`?path=Knowledge/Examples/Device.md`), not as part of the URL path — see
`docs/PHASE1_PLAN.md` section 4.5 for why. `POST /api/v1/inbox/notes/append`
follows the same reasoning: the target note's path is a JSON body field
(`path`), not a URL path segment.

`GET /api/v1/search` and `GET /api/v1/vault/tree` both support an opaque
`cursor` query parameter for pagination — take the previous response's
`next_cursor` and pass it back with the same other query parameters to get
the next page. A cursor is bound to those parameters (and to the current
`API_TOKEN`) and is rejected with `INVALID_CURSOR` if either changes.

## Configuration

Copy `.env.example` to `.env` and fill it in:

```bash
cp .env.example .env
openssl rand -hex 32   # use the output as API_TOKEN
```

See `.env.example` for every variable. Required for a real deployment:
`API_TOKEN`, `MCP_ALLOWED_HOSTS`, `VAULT_HOST_PATH` / `INBOX_HOST_PATH`.

`AUTH_ENABLED` (default `true`) gates bearer-token enforcement on both REST
and MCP; see "Security invariants" above for when `false` is appropriate.
`API_TOKEN` stays required either way — it also signs pagination cursors.
Disabling it logs a `WARNING authentication_disabled` line once at startup
(see Logging below), so a deployment running without auth is always visible
in `docker logs`.

## Logging

Aligned plain text on **stdout** — nothing is ever written to a file (the
container filesystem is `read_only`), so Docker's log driver is the whole
story: `docker logs obsidian-api`, Portainer's Logs tab, or OMV's Compose
plugin all show it.

```text
2026-08-02T21:13:58.001+0900  INFO  uvicorn Started server process [1]
2026-08-02T21:14:03.412+0900  INFO  rest    GET        /api/v1/notes              200          12.4ms   note=Knowledge/Examples/Device.md
2026-08-02T21:14:05.100+0900  INFO  rest    GET        /api/v1/search             200          48.2ms   q_len=29 results=5
2026-08-02T21:14:07.883+0900  INFO  mcp     tools/call search_notes               success      31.7ms   results=5
2026-08-02T21:14:12.004+0900  INFO  mcp     tools/call read_note                  error        3.1ms
2026-08-02T21:14:19.002+0900  INFO  mcp     -          mcp_auth_failed            unauthorized -        reason=bearer_token_mismatch
```

| Field | Meaning |
|---|---|
| `$1` | Timestamp, ISO 8601 in the container's `TZ`. `T`, not a space, so it stays one field |
| `$2` | Level (`DEBUG`/`INFO`/`WARN`/`ERROR`) |
| `$3` | Source: `rest` or `mcp` for the access logs, otherwise `uvicorn` / `mcp-sdk` / `app` |
| `$4` | Method: HTTP verb, or `tools/call` |
| `$5` | Target: full REST route, MCP tool name, or the event when there is neither |
| `$6` | Status: HTTP status, or `success` / `error` / `unauthorized` |
| `$7` | Duration |
| rest | `key=value` for whatever optional fields the event has |

Every column is exactly one whitespace-separated field, so `awk` works
directly. Only the tail can contain spaces, and the two fields there that can
(`note`, `detail`) come last:

```bash
docker logs obsidian-api | awk '$3=="mcp" {print $5}' | sort | uniq -c   # calls per tool
docker logs obsidian-api | awk '$3=="mcp" && $6=="error"'                # failed tool calls
docker logs obsidian-api | grep mcp_auth_failed                          # rejected requests
```

`LOG_LEVEL` (default `INFO`) is applied to the `obsidian_gateway` loggers.
`DEBUG` additionally surfaces the `/api/v1/health` line, which is deliberately
below `INFO`: the Docker `HEALTHCHECK` hits that route every 30 seconds and at
`INFO` it accounted for almost the whole log.

What never appears, per `AGENTS.md` and `docs/IMPLEMENTATION_PLAN.md`
section 14: the bearer token, note content, frontmatter, the search term (only
its length), MCP request/response bodies, and absolute host paths. The
formatter renders an explicit allow-list of fields rather than dumping
whatever a caller attached, so adding a new `extra` somewhere cannot leak it by
accident. uvicorn's own access log is silenced in two places — the Dockerfile's
`--no-access-log` and `app/logging_config.py` — because its formatter prints
the raw query string.

`compose.yaml` caps retention at `10m × 3` files (30 MB). Without that,
`json-file` keeps every line forever.

## Running locally (no Docker)

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'

API_TOKEN=$(openssl rand -hex 32) \
MCP_ALLOWED_HOSTS=localhost,127.0.0.1 \
VAULT_READ_ROOT=/path/to/a/test/vault \
VAULT_INBOX_ROOT=/path/to/a/test/vault/00_Inbox/ChatGPT \
  .venv/bin/uvicorn app.main:app --reload
```

**Never point `VAULT_READ_ROOT` at a real vault for anything other than
manual, careful checking.** Automated tests never do this — see Testing below.

Smoke-test both transports:

```bash
curl -fsS http://127.0.0.1:8000/api/v1/health

curl -fsS -X POST http://127.0.0.1:8000/mcp/ \
     -H "Authorization: Bearer $API_TOKEN" \
     -H 'Content-Type: application/json' \
     -H 'Accept: application/json, text/event-stream' \
     -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'
```

(`/mcp/` with the trailing slash avoids a redirect round trip; `/mcp` also
works.)

## Testing

```bash
.venv/bin/pytest -q
.venv/bin/ruff check .
.venv/bin/python scripts/export_openapi.py --check
```

Every test runs against a disposable vault built under `tmp_path`
(`tests/conftest.py`), never a real vault. `tests/fixtures/vault/` holds only
plain committed files; symlinks and oversized notes needed for edge-case tests
are generated at test time, not committed.

MCP tests (`tests/test_mcp_*.py`) mostly share one session-scoped `mcp_client`
fixture rather than one per test: the SDK's session manager can only be
started once per process, matching how a real server runs. `test_mcp_tools.py`
covers the seven tools directly, unmounted; `test_mcp_lifespan.py` and two
tests in `test_mcp_protocol.py` that need their own event loop build an
independent, throwaway MCP server instead.

## Docker / Compose

```bash
docker compose config    # validate before starting anything
docker compose pull
docker compose up -d
```

The image is a non-root, `read_only: true` container with all Linux
capabilities dropped (see `Dockerfile` / `compose.yaml`). The vault is never
copied into the image — it is bind-mounted at runtime. The container's own
port (8000) is never published to the host; only Caddy, on the existing
`PROXY_NETWORK` Docker network, reaches it.

### Container memory limit

`compose.yaml` sets `mem_limit: 512m`. This is a last line of defence, not
a substitute for the app-level bounds that keep normal operation far under
it (the frontmatter conversion budget in `app/services/markdown_parser.py`,
per-request size caps) — it exists in case one of those bounds turns out to
be wrong, or a future change adds an unbounded path.

512 MiB is an initial default, not a measured ceiling for every deployment:
against a synthetic 3000-note vault, this process's steady-state RSS after
importing and running several full-vault searches was about 78 MiB, leaving
roughly 6x headroom. A vault with unusually large notes, or a search whose
result set is unusually large, could use more. If the container is OOM
killed, do not simply raise the limit — first check the log for which
request was in flight, how large the vault/notes involved are, and how many
requests were running concurrently (`VAULT_SCAN_CONCURRENCY` in
`app/main.py` bounds full-vault scans to 2 at a time); raise the limit only
once that points at a real, expected memory need rather than a leak.

> **Not verified in this repository's automated tests.** The development
> environment this code was written in has no Docker installed, so nothing
> below is exercised by `pytest`. The checklist has been run manually on the
> OMV host; only the container memory limit and Docker log-rotation settings
> added later remain unverified against the latest image.

### OMV verification checklist

All `curl` commands use `-fsS` so a failure shows up as a non-zero exit code
instead of scrolling past silently.

```bash
docker compose config

docker compose pull

docker compose up -d

BASE=https://obsidian-api.example.com
NOTE='<a note path that already exists in your vault>'
QUERY='<a term that matches several notes in your vault>'

curl -fsS "$BASE/api/v1/health"

# Skip these two if NOTE/QUERY are still placeholders — the literal values
# above match nothing, and `curl -fsS` would report a 404 as a real failure.
case "$NOTE$QUERY" in
  *'<'*)
    echo 'Set NOTE and QUERY to real values in your vault first — skipped.' >&2
    ;;
  *)
    curl -fsS -H "Authorization: Bearer $API_TOKEN" --get "$BASE/api/v1/search" \
         --data-urlencode "q=$QUERY" --data-urlencode 'limit=5'

    curl -fsS -H "Authorization: Bearer $API_TOKEN" --get "$BASE/api/v1/notes" \
         --data-urlencode "path=$NOTE"
    ;;
esac

curl -fsS -H "Authorization: Bearer $API_TOKEN" --get "$BASE/api/v1/vault/tree" \
     --data-urlencode 'limit=100'

curl -fsS -H "Authorization: Bearer $API_TOKEN" "$BASE/api/v1/vault/summary"

curl -fsS -X POST -H "Authorization: Bearer $API_TOKEN" -H 'Content-Type: application/json' \
     -d '{"title":"Gateway smoke test","content":"# Gateway smoke test\n"}' \
     "$BASE/api/v1/inbox/notes"

curl -fsS -X POST -H "Authorization: Bearer $API_TOKEN" -H 'Content-Type: application/json' \
     -d '{"path":"00_Inbox/ChatGPT/Gateway smoke test.md","content":"\nAppended by the OMV checklist.\n"}' \
     "$BASE/api/v1/inbox/notes/append"

# MCP: no Bearer → 401; tools/list → 7 tools
curl -i -X POST "$BASE/mcp/" -H 'Content-Type: application/json' \
     -H 'Accept: application/json, text/event-stream' \
     -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'

curl -fsS -X POST "$BASE/mcp/" -H "Authorization: Bearer $API_TOKEN" \
     -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
     -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'
```

Walk the vault tree a level at a time to confirm pagination works end to
end: request `/api/v1/vault/tree` with a small `limit`, follow `next_cursor`
until it comes back null, and confirm every entry was seen exactly once.
Repeat for `/api/v1/search` with `$QUERY` and a `limit` smaller than its
match count.

**The `POST /api/v1/inbox/notes` and any `create_inbox_note` calls above
create a real note in `00_Inbox/ChatGPT` on your vault; `append_inbox_note`
/ the append call above appends to it.** There is no delete endpoint or tool
(by design — see Security invariants above), so the Gateway itself cannot
remove it. After confirming it synced correctly, delete the note manually,
from Obsidian or directly on the OMV host. Do the same for any note created
while checking LiveSync below.

Also confirm `append_inbox_note` / `POST /api/v1/inbox/notes/append` reject
a path outside `00_Inbox/ChatGPT` (e.g. `Knowledge/...`) with
`PATH_OUTSIDE_VAULT`.

Container permission checks:

```bash
docker compose exec obsidian-api sh -c '
  id
  touch /vault-ro/should-fail 2>&1
  touch /vault-write/inbox/should-succeed && rm /vault-write/inbox/should-succeed
'
```

Expect: a non-root uid/gid, the `/vault-ro` write to fail, and the
`/vault-write/inbox` write/remove to succeed.

**Create mode policy**: a newly created note (`create_inbox_note` /
`POST /api/v1/inbox/notes`) is always written with mode `0o644` — matching
the mode an ordinary note in the vault has — regardless of the container
process's umask. An appended-to note instead keeps whatever mode it already
had before the append (see below); append never changes a note's mode.

**Append and ownership** (`docs/adr/0003-allow-os-replace-for-inbox-append.md`):
`append_inbox_note` uses `os.replace()`, which preserves the note's file
mode but not its owning UID/GID — the appended note's owner becomes whoever
the container process runs as. Before relying on append in production,
confirm on the OMV host:

```bash
ls -ln /path/to/vault/00_Inbox/ChatGPT/'Gateway smoke test.md'   # before append
# ... run the append curl command above ...
ls -ln /path/to/vault/00_Inbox/ChatGPT/'Gateway smoke test.md'   # after append
```

and note whether the uid/gid columns changed. If they did and that note is
no longer writable by the same host-side user/process that Obsidian or
LiveSync runs as, append is not safe to use in that deployment as-is (see
the ADR's Consequences section).

LiveSync check — with the container running, use a smoke-test write above (or
`create_inbox_note` from an actual client) and confirm, in order:

1. The note appears on the server's vault filesystem.
2. `livesync-cli` detects the change.
3. It syncs to CouchDB.
4. It appears in Obsidian on a PC.
5. It appears in Obsidian on an iPhone.

Then repeat the same five checks for an **append** (the append curl command
above, or `append_inbox_note` from an actual client) against that same
note, and additionally confirm:

6. The note is still editable and saveable from Obsidian on the PC after
   the append (catches the ownership change above silently breaking
   host-side writes).
7. The note is still editable and saveable from Obsidian on the iPhone
   after the append.

Then delete the test note as noted above.

Client checks: MCP Inspector connects and lists all seven tools; ChatGPT
desktop connects and a read tool runs without a prompt; Codex CLI connects
(`codex mcp list`) and both write tools (`create_inbox_note`,
`append_inbox_note`) prompt for approval before running.

### Updating to a new image

After a `main` push, GitHub Actions (`.github/workflows/publish.yml`) builds
and publishes a new `ghcr.io/vivittel/obsidian-vault-gateway:latest`. Apply
it on OMV:

```bash
docker compose pull
docker compose up -d --force-recreate
```

If the GHCR package is **public** (this repo's default), pulling it needs no
`docker login` — only *pushing* to it, done by CI with the ephemeral
`secrets.GITHUB_TOKEN`, needs credentials. If the package is private,
authenticate once with a PAT that has `read:packages`:

```bash
echo "$GHCR_READ_PAT" | docker login ghcr.io -u <github-username> --password-stdin
```

Confirm the new container is healthy and still enforces auth:

```bash
BASE=https://obsidian-api.example.com

curl -fsS "$BASE/api/v1/health"

# No Bearer token → 401. A silently-broken auth check after an update is the
# dangerous failure mode this guards against — a 200 here would mean /mcp is
# reachable unauthenticated.
curl -i -X POST "$BASE/mcp/" -H 'Content-Type: application/json' \
     -H 'Accept: application/json, text/event-stream' \
     -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'
```

### Rolling back

Every published image also carries its commit SHA as a tag (`type=sha` in
`.github/workflows/publish.yml`), independent of `:latest`. To roll back,
pin `OBSIDIAN_GATEWAY_IMAGE` in `.env` to a known-good SHA instead of
overwriting `:latest`:

```bash
# find a known-good commit (git log, or the package page on GHCR)
echo "OBSIDIAN_GATEWAY_IMAGE=ghcr.io/vivittel/obsidian-vault-gateway:<sha>" >> .env

docker compose pull
docker compose up -d --force-recreate
```

There is no schema or database migration to reverse — the only state a
rollback needs to account for is any note `create_inbox_note` already wrote
to `00_Inbox/ChatGPT` while the newer image was running, which nothing here
removes. Switch `OBSIDIAN_GATEWAY_IMAGE` back to `:latest` (or delete the
override) once a fixed image is published.

## Caddy

Example site block: `docs/caddy/obsidian-api.Caddyfile`. Requirements it
covers: HTTPS-only, only `/mcp` (primary) and `/api/v1/*` (diagnostics)
served on this host name, a request-size cap matching `MAX_REQUEST_BYTES`
exactly, and access logging. Bearer token checking stays inside the
application. `MCP_ALLOWED_HOSTS` on the container must include this host
name, or the MCP transport's own DNS-rebinding protection rejects every
request Caddy forwards.

## Known gaps (tracked for later phases)

- Phase 2's OMV/LiveSync verification checklist above — including the append
  ownership check — has been run on real hardware. What remains is the
  container memory limit (`mem_limit: 512m`) and the Docker `json-file` log
  rotation (`max-size: 10m`, `max-file: 3`), which were added afterwards and
  have not yet been checked against the latest deployed image. Large-vault
  performance work (`docs/IMPLEMENTATION_PLAN.md` section 18's
  "大規模Vault向け改善", e.g. a SQLite FTS5 index) is explicitly out of
  Phase 2's scope.
- Rate limiting, concurrency limits, metrics, MCP compatibility testing
  across SDK updates — Phase 3.
- Vault audit (orphan notes, broken links, stale Inbox notes) — Phase 4.
- MCP resources, prompts, OAuth, a public/tunnelled deployment — out of scope
  for the foreseeable future; see ADR-0001's "Review conditions".
