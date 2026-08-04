# Obsidian Vault Gateway

A secure gateway over a private Obsidian vault: full-vault search, note
reads, staged directory/summary browsing, and note creation and append
restricted to one directory. **MCP is the primary interface** — for the
ChatGPT desktop app, Codex CLI, and the Codex IDE extension, all sharing one
MCP server configuration on the same Codex host, without exposing the
Gateway to the public internet. A secondary REST API is kept for health
checks, curl-based diagnostics, and regression tests.

This is **Phase 2** of `docs/IMPLEMENTATION_PLAN.md` — implemented and
covered by the automated test suite, with OMV/LiveSync deployment
verification still pending (see the checklist below). See
`docs/adr/0001-switch-primary-interface-to-mcp.md` for why MCP replaced the
original ChatGPT Actions plan, `docs/adr/0002-use-mcp-python-sdk-v2.md` for
why this runs on the MCP Python SDK's v2 line,
`docs/adr/0003-allow-os-replace-for-inbox-append.md` for why note append is
the one place `os.replace()` is used, and `docs/MCP_IMPLEMENTATION_PLAN.md`
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

MCP and REST call the same `app/application.py` and service functions
(`app/services/`); neither transport calls the other over HTTP, so they can
never diverge in behaviour for the same operation.

## MCP (primary interface)

Endpoint: `/mcp`, Streamable HTTP transport, Bearer token authentication
(same `API_TOKEN` as REST). Stateless (`stateless_http=True`): no session is
tracked across requests, so terminating one (`DELETE`) is a no-op, not an
error.

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

Full schema: `openapi.json` (regenerate with `scripts/export_openapi.py`
after changing any router/model), or `GET /docs` on a running instance.

`GET /api/v1/notes` takes the note path as a **query parameter**
(`?path=Knowledge/PC/GPU/RTX 5070.md`), not as part of the URL path — see
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
> environment this code was written in has no Docker installed, so
> `docker compose config`, the container-permission checks below, and the
> LiveSync check have not been run. Run the checklist below on the actual OMV
> host before relying on this in production.

### OMV verification checklist

All `curl` commands use `-fsS` so a failure shows up as a non-zero exit code
instead of scrolling past silently.

```bash
docker compose config

docker compose pull

docker compose up -d

BASE=https://obsidian-api.example.com

curl -fsS "$BASE/api/v1/health"

curl -fsS -H "Authorization: Bearer $API_TOKEN" --get "$BASE/api/v1/search" \
     --data-urlencode 'q=RTX 5070' --data-urlencode 'limit=5'

curl -fsS -H "Authorization: Bearer $API_TOKEN" --get "$BASE/api/v1/notes" \
     --data-urlencode 'path=Knowledge/PC/GPU/RTX 5070.md'

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
Repeat for `/api/v1/search` with a query that matches more notes than
`limit`.

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

- Phase 2 (`get_vault_tree`, `get_vault_summary`, `append_inbox_note`,
  search/tree cursor pagination) is implemented and covered by the
  automated test suite, but the OMV/LiveSync deployment verification
  checklist above — including the append ownership check — has not yet been
  run on real hardware. Large-vault performance work (`docs/IMPLEMENTATION_
  PLAN.md` section 18's "大規模Vault向け改善", e.g. a SQLite FTS5 index) is
  explicitly out of Phase 2's scope.
- Rate limiting, concurrency limits, metrics, MCP compatibility testing
  across SDK updates — Phase 3.
- Vault audit (orphan notes, broken links, stale Inbox notes) — Phase 4.
- MCP resources, prompts, OAuth, a public/tunnelled deployment — out of scope
  for the foreseeable future; see ADR-0001's "Review conditions".
