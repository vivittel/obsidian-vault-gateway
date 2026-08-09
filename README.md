# Obsidian Vault Gateway

A secure gateway over a private Obsidian vault: full-vault search, note
reads, staged directory/summary browsing, and note creation and append
restricted to one directory. **MCP is the sole functional interface** — for
the ChatGPT desktop app, Codex CLI, and the Codex IDE extension, all sharing
one MCP server configuration on the same Codex host, without exposing the
Gateway to the public internet. REST is health-only
(`docs/adr/0010-reduce-rest-surface-to-health-only.md`): `GET /api/v1/health`
for `docker healthcheck`, Caddy, and curl-based diagnostics — nothing else.

**Phase 2** of `docs/IMPLEMENTATION_PLAN.md` is complete — implemented,
covered by the automated test suite, and verified on the OMV host together
with LiveSync, Obsidian on a PC, and Obsidian on an iPhone — and several
Phase 3 items (`docs/IMPLEMENTATION_PLAN.md` section 18) have shipped ahead
of the rest: the shared vault-scan concurrency limit, MCP tool-usage
logging, and scoped duplicate-note detection (`find_duplicate_candidates`).
Only the container memory limit and Docker log-rotation settings added
afterwards remain to be checked against the latest deployed image (see the
checklist below). See `docs/adr/0001-switch-primary-interface-to-mcp.md` for
why MCP replaced the original ChatGPT Actions plan,
`docs/adr/0002-use-mcp-python-sdk-v2.md` for why this runs on the MCP Python
SDK's v2 line, `docs/adr/0003-allow-os-replace-for-inbox-append.md` for why
note append is the one place `os.replace()` is used,
`docs/adr/0004-allow-disabling-bearer-authentication.md` for when and how
bearer authentication may be disabled,
`docs/adr/0005-single-structured-entry-point-for-chat-exports.md` for why
`create_inbox_note` renders structured chat exports instead of exposing a
second write tool, `docs/adr/0006-verified-related-note-wikilinks.md` for how
`export.related_notes` is re-verified against the Vault before it is
rendered, `docs/adr/0007-scoped-duplicate-note-detection.md` for why
duplicate detection is its own read-only tool rather than a check embedded in
`create_inbox_note`, `docs/adr/0008-normalize-bare-mcp-path.md` for why the
bare `/mcp` path is normalized in-scope rather than redirected,
`docs/adr/0009-verbatim-code-blocks-in-structured-exports.md` for how
`procedure.steps` preserves code content and step ordering,
`docs/adr/0010-reduce-rest-surface-to-health-only.md` for why REST was
reduced to `GET /api/v1/health`, and `docs/MCP_IMPLEMENTATION_PLAN.md` for
the MCP design in full. Phase 1 and Phase 1.5 (the REST-only and
MCP-introduction predecessors) are documented as completed history in
`docs/PHASE1_PLAN.md` and `docs/IMPLEMENTATION_PLAN.md`.

## Security invariants

These hold regardless of what future phases add (see `AGENTS.md`):

- The whole vault is mounted **read-only**.
- Only `00_Inbox/ChatGPT` is writable, and only through the `create_inbox_note`
  / `append_inbox_note` MCP tools. `append_inbox_note` can only extend an
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

`app/application.py` (`GatewayApplication`) is transport-neutral. REST's own
health route and the MCP tools both call it and the service functions it
wraps (`app/services/`) directly; neither transport calls the other over
HTTP.

## MCP (primary interface)

Endpoint: `/mcp`, Streamable HTTP transport, Bearer authentication by default
(`API_TOKEN`; runtime-disableable with `AUTH_ENABLED=false`, see
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
| `find_duplicate_candidates` | read | auto |
| `create_inbox_note` | write | **prompt** |
| `append_inbox_note` | write | **prompt** |

`search_notes` before `read_note` when the exact path is unknown — search
results' `path` can be passed directly to `read_note`, `get_vault_tree`'s
`folder`, or `append_inbox_note`'s `path`. `get_vault_tree` lists one
folder's direct children at a time (folders before notes) and supports
cursor-based pagination — pass a non-null `next_cursor` back as `cursor`
with the same `folder` to fetch the next page; a cursor is only valid for
the exact arguments it was minted with, and becomes invalid if `API_TOKEN`
is rotated. `get_vault_summary` gives vault-wide counts, sizes, and top
tags without exposing any note body or title, and does not paginate — it
has no `cursor`, only `top_tags_limit` (1-200, default 20) to bound how
many tags come back. `create_inbox_note` always writes a new file under
`00_Inbox/ChatGPT`; `append_inbox_note` appends to an existing file already
directly inside it. Neither can overwrite, delete, move, or rename notes,
and `create_inbox_note` does not accept a path from the caller.
`create_inbox_note` needs no lock: `os.link()` itself atomically detects a
name collision (`FileExistsError`), so two concurrent creates for the same
title just retry onto the next free sequence number rather than racing.
`append_inbox_note` does serialise on one inbox-wide lock (two appends
could otherwise race on the same file); a request that cannot get it
within a few seconds fails with `INBOX_LOCK_TIMEOUT` (`503`) rather than
blocking indefinitely — this is the one error code safe to retry as-is,
with no change to the request.

**`create_inbox_note` takes a structured summary, not raw Markdown** (issue
#12 / `docs/adr/0005-*.md`). The client reads the conversation, picks an
`export.mode`, and fills in the fields that mode defines; the Gateway
performs no summarisation of its own and instead renders that input into a
fixed section order and a fixed frontmatter schema. `export.mode` defaults to
`summary`, so a plain "summarise this and save it" request needs no explicit
mode. The seven modes are `summary`, `technical`, `history`, `full`,
`procedure`, `issue`, and `reference` — each mode's own fields are described
in the tool's generated input schema. Every export gets these headings, in
this fixed order, regardless of mode:

| Order | Heading | Notes |
|---|---|---|
| 1 | `## 要約` | Required; 2–5 sentences summarising the conversation |
| 2 | `## 決定事項` | Adopted conclusions only; `なし` when empty |
| 3 | *(mode-specific headings)* | See the tool's input schema for each mode's fields |
| 3.5 | `## コード` | Optional; present only when `export.code_blocks` is non-empty (see below) — omitted entirely otherwise, in every mode |
| 4 | `## 未解決の論点` | Open questions, never merged with next actions; `なし` when empty |
| 5 | `## 次のアクション` | Concrete next steps; `なし` when empty |
| 6 | `## 関連ノート` | Verified wikilinks, or `なし` when none survive verification |
| 7 | `## 出典` | `なし` when empty |

`## 未解決の論点`/`## 次のアクション`/`## 決定事項`/`## 出典` use the
placeholder `なし` when empty (nothing to record is complete information);
most mode-specific sections use `未記録` (not captured); `## 原因`
(`issue` mode's `root_cause`) uses `未解決` specifically, since an empty
cause means the cause was not established. Frontmatter is generated with a
stable key order — `title`, `created`, `updated`, `source: chatgpt`,
`export_mode`, optionally `project` and `conversation_type`, then `tags` —
and none of those keys can be supplied as free-form `frontmatter` alongside
`export`.

**`procedure.steps` preserves code content and step order verbatim/
structure-preservingly, not just as one flattened line** (issue #12 follow-up
/ `docs/adr/0009-*.md`). Each step is an ordered list of blocks —
`{"type": "text", "content": ...}` or
`{"type": "code", "language": ..., "label": ..., "content": ...}` — so a step
can interleave explanation and commands in the order the conversation had
them ("open the file, edit it, restart it") instead of losing indentation,
blank lines, and fence markers to the single-line rendering every other
field uses. A bare string is still accepted as a backward-compatible
shorthand for a step with one text block — every export with no code renders
exactly as before this feature existed. Code that does not belong to any
single step (a finished config file, a complete script, an appendix log) can
instead go in the top-level `export.code_blocks`, available in every mode,
rendered as the optional `## コード` section above — never both: code that
belongs to a step stays in that step, so the procedure keeps its order.

**Related notes are client-selected, Gateway-verified** (issue #13 /
`docs/adr/0006-*.md`). The client calls `search_notes`, picks relevant
results, and passes their vault-relative `.md` paths in
`export.related_notes` (usually 3-5, at most 10) — it never invents a path.
The Gateway re-verifies every path against the Vault at write time and
renders only the survivors as `[[Vault/relative/path]]` wikilinks — the full
path, with no alias, so a link never depends on a basename lookup and two
notes sharing a basename in different folders still resolve unambiguously.
A single candidate that no longer resolves, is a duplicate, or is
syntactically hazardous (e.g. contains `[`, `]`, `|`, `#`, or `^`) is
silently omitted rather than blocking the save; the response's
`related_notes_linked` / `related_notes_skipped` counts — not the input —
are the record of what was actually linked. Submitting *more than* the
documented maximum is a different, harder failure: unlike an individual bad
path, an over-count list is rejected outright (the whole request fails
validation), the same way every other list field on `export` already
behaves. This canonical format governs only links the Gateway itself
renders; pre-existing hand-authored wikilinks elsewhere in the Vault are
untouched.

**Duplicate detection before creating a note is scoped and advisory, not a
gate** (issue #14 / `docs/adr/0007-*.md`). `find_duplicate_candidates` scans
only the direct children of `00_Inbox/ChatGPT` — never the rest of the Vault
— and reads only frontmatter, never a note's body. It compares a proposed
`title` (exact and normalized), `project`, and `keywords` (at most 10) —
matched against a candidate's `title`/`tags`, never its body — against each
existing note's frontmatter `title`/`project`/`tags`, and returns up to
`limit` candidates (default 5, max 10) plus a `recommendation`:

| `recommendation` | Meaning | Client action |
|---|---|---|
| `create` | No credible duplicate (a `low`-confidence candidate may still be listed) | Call `create_inbox_note` without asking |
| `confirm` | Exactly one `high`-confidence candidate and nothing else at `high`/`medium` | Ask the user to choose new / append / cancel before writing anything |
| `choose` | Several or ambiguous candidates | Show the candidates and require an explicit pick before writing anything |

`recommendation` is decided from every matching candidate, before `limit`
ever truncates the list — the response's `candidate_count` and `truncated`
report the full picture even when `candidates` itself is shorter. The
Gateway itself never blocks `create_inbox_note`/`append_inbox_note` on this
result — similarity is advisory, not write authorization — so this is a
client-workflow contract, documented on the write tools themselves and in
the MCP server's instructions: on `confirm`/`choose`, neither write tool is
called until the user has explicitly picked new/append/cancel; on `create`,
proceed without asking; if the tool itself fails, proceed with
`create_inbox_note` as normal unless the user asked for strict duplicate
checking.

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
`get_vault_tree`, `get_vault_summary`, `find_duplicate_candidates`) run
without confirmation; `create_inbox_note` and `append_inbox_note` prompt
before writing.

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
A plain "summarise and save" prompt like this one exercises the default
`export.mode: summary` path.

### MCP Inspector

```bash
npx -y @modelcontextprotocol/inspector
```

Connect to `https://obsidian-api.example.com/mcp` (Streamable HTTP),
set the Bearer token, and confirm all eight tools are listed and callable.

### Configuration

`MCP_ALLOWED_HOSTS` (required) is a comma-separated Host-header allowlist for
the transport's DNS-rebinding protection. Without it, the SDK defaults to
allowing only `localhost`/`127.0.0.1`, which silently rejects every request
Caddy forwards with a real Host header — set it to whatever hostname(s) the
Gateway is actually reached by, e.g. `obsidian-api.example.com`. See
`.env.example`.

## REST (health-only)

`docs/adr/0010-reduce-rest-surface-to-health-only.md` records why: MCP is
the sole functional interface, and REST's job is limited to
`docker healthcheck`, Caddy, and curl-based diagnostics.

| Method | Path | operationId | Auth |
|---|---|---|---|
| GET | `/api/v1/health` | `getHealth` | none |

`GET /api/v1/health` always answers HTTP 200 — even when a mount is missing or
has the wrong permissions, `status` in the body is `"degraded"` instead (see
the response schema). The Dockerfile's `HEALTHCHECK` treats that the same as
an unreachable server: it parses the body and only exits 0 when `status` is
`"ok"`, so a container with a broken vault or inbox mount shows as `unhealthy`
(`docker ps`, Portainer, OMV's Compose plugin) rather than healthy. This does
**not** restart the container by itself — `restart: unless-stopped`
(`compose.yaml`) only restarts on process exit, not on an `unhealthy` status —
so treat `unhealthy` as something to go look at, not something that
self-heals.

Full schema: `openapi.json` (regenerate with `scripts/export_openapi.py`
after changing `app/routers/health.py`/`app/models.py`), or `GET /docs`/
`GET /redoc`/`GET /openapi.json` on a running instance — FastAPI serves all
three with no Bearer requirement, same as `/api/v1/health`, and this is
unaffected by the REST surface reduction. The example Caddy site block
(`docs/caddy/obsidian-api.Caddyfile`) only proxies `/mcp` and
`/api/v1/health` and 404s everything else, so these three stay unreachable
in that deployment; a deployment that proxies more of the app than that
example does would reach them without a token.

## Configuration

Copy `.env.example` to `.env` and fill it in:

```bash
cp .env.example .env
openssl rand -hex 32   # use the output as API_TOKEN
```

See `.env.example` for every variable. Required for a real deployment:
`API_TOKEN`, `MCP_ALLOWED_HOSTS`, `VAULT_HOST_PATH` / `INBOX_HOST_PATH`.

`.env` is read by **Docker Compose** to fill in `compose.yaml`'s
`${VARIABLE}` references — the application itself (`app/config.py`'s
`Settings`) never reads that file; it only reads whatever environment
variables the process was actually started with. `docker compose up` gets
both for free from the same `.env`. Running `uvicorn` directly instead
("Running locally" below) needs those variables passed on the command line
or exported into the shell — `cp .env.example .env` alone does nothing for
that path.

`AUTH_ENABLED` (default `true`) gates bearer-token enforcement on `/mcp`
(REST is health-only and never requires a token — docs/adr/0010-*.md); see
"Security invariants" above for when `false` is appropriate. `API_TOKEN`
stays required either way — it also signs pagination cursors.
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
2026-08-02T21:14:03.412+0900  DEBUG rest    GET        /api/v1/health             200          1.1ms
2026-08-02T21:14:07.883+0900  INFO  mcp     tools/call search_notes               success      31.7ms   q_len=12 results=5
2026-08-02T21:14:12.004+0900  INFO  mcp     tools/call read_note                  error        3.1ms    code=NOTE_NOT_FOUND
2026-08-02T21:14:19.002+0900  INFO  mcp     -          mcp_auth_failed            unauthorized -        reason=bearer_token_mismatch
```

REST's own line is logged at `DEBUG`, not `INFO` (see below) — with the
default `LOG_LEVEL=INFO` you will not see it at all; `LOG_LEVEL=DEBUG`
surfaces it exactly as shown above, still at level `DEBUG`.

| Field | Meaning |
|---|---|
| `$1` | Timestamp, ISO 8601 in the container's `TZ`. `T`, not a space, so it stays one field |
| `$2` | Level (`DEBUG`/`INFO`/`WARN`/`ERROR`/`CRIT`) |
| `$3` | Source: `rest` or `mcp` for the access logs, otherwise `uvicorn` / `mcp-sdk` / `app` |
| `$4` | Method: HTTP verb, or `tools/call` |
| `$5` | Target: `/api/v1/health` (REST's only route), MCP tool name, or the event when there is neither |
| `$6` | Status: HTTP status, or `success` / `error` / `unauthorized` |
| `$7` | Duration |
| rest of line | `key=value` for whatever optional fields the event has |

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

(`/mcp` without the trailing slash works identically — the ASGI layer
rewrites it in place before routing, not via an HTTP redirect, so there is
no extra round trip and no unauthenticated window either way.)

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
covers the eight tools directly, unmounted; `test_mcp_lifespan.py` and two
tests in `test_mcp_protocol.py` that need their own event loop build an
independent, throwaway MCP server instead.

`tests/test_chat_export.py` covers the structured-chat-export formatter
(`app/services/chat_export.py`) in isolation — a pure function of its
arguments, so it needs no fixtures beyond a fixed clock: every mode's
headings and required fields, empty-state placeholders, frontmatter key
order and omission rules, tag normalisation, and the cases where validation
must run against normalised text rather than the raw request (a field that
is non-empty before normalisation but empty after it, e.g. `steps: ["\n"]`).
It also covers `procedure.steps`' verbatim/structure-preserving code content
(`docs/adr/0009-*.md`) — the `markdown-it-py` dev dependency parses rendered
output to confirm a rendered note's *structure*, not just its raw text:
step numbering never breaks (including step 10 and beyond, whose 4-character
marker needs a wider continuation indent than steps 1-9), a code fence never
closes early on embedded backtick runs, and a step's later text stays in the
same list item as its earlier code.

`tests/test_related_notes.py` covers `app/services/related_notes.py` —
verified related-note wikilink resolution (issue #13 / `docs/adr/0006-*.md`)
— against disposable vaults built directly under `tmp_path`, including
ambiguous same-basename paths, hazardous filenames, duplicate/hardlinked
targets, and the maximum-link boundary; it deliberately never adds files to
the shared `tests/fixtures/vault/` tree other tests' counts depend on.

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
`app/runtime.py` bounds MCP's full-vault-scanning tools to 2 concurrent
scans at a time); raise the limit only once that points at a real, expected
memory need rather than a leak.

> **Not verified in this repository's automated tests.** The development
> environment this code was written in has no Docker installed, so nothing
> below is exercised by `pytest`. The checklist has been run manually on the
> OMV host; only the container memory limit and Docker log-rotation settings
> added later remain unverified against the latest image.

### OMV verification checklist

All `curl` commands use `-fsS` so a failure shows up as a non-zero exit code
instead of scrolling past silently. The commands below that embed a
variable value (`$QUERY`, `$NOTE`, `$REAL_NOTE_PATH`) into a JSON-RPC body
build it with `jq -n` rather than string-concatenating it into a
single-quoted literal — a value containing `"` or `\` would otherwise
produce invalid JSON. `jq` is assumed to be available on the OMV host for
this reason.

```bash
docker compose config

docker compose pull

docker compose up -d

BASE=https://obsidian-api.example.com
NOTE='<a note path that already exists in your vault>'
QUERY='<a term that matches several notes in your vault>'

curl -fsS "$BASE/api/v1/health"

# MCP: no Bearer → 401; tools/list → 8 tools
curl -i -X POST "$BASE/mcp/" -H 'Content-Type: application/json' \
     -H 'Accept: application/json, text/event-stream' \
     -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'

curl -fsS -X POST "$BASE/mcp/" -H "Authorization: Bearer $API_TOKEN" \
     -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
     -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'

# Skip these two if NOTE/QUERY are still placeholders — the literal values
# above match nothing, and a tool error would report as a real failure.
case "$NOTE$QUERY" in
  *'<'*)
    echo 'Set NOTE and QUERY to real values in your vault first — skipped.' >&2
    ;;
  *)
    curl -fsS -X POST "$BASE/mcp/" -H "Authorization: Bearer $API_TOKEN" \
         -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
         -d "$(jq -n --arg q "$QUERY" \
              '{jsonrpc:"2.0",id:1,method:"tools/call",
                params:{name:"search_notes",arguments:{query:$q,limit:5}}}')"

    curl -fsS -X POST "$BASE/mcp/" -H "Authorization: Bearer $API_TOKEN" \
         -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
         -d "$(jq -n --arg p "$NOTE" \
              '{jsonrpc:"2.0",id:1,method:"tools/call",
                params:{name:"read_note",arguments:{path:$p}}}')"
    ;;
esac

curl -fsS -X POST "$BASE/mcp/" -H "Authorization: Bearer $API_TOKEN" \
     -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
     -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"get_vault_tree","arguments":{"limit":100}}}'

curl -fsS -X POST "$BASE/mcp/" -H "Authorization: Bearer $API_TOKEN" \
     -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
     -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"get_vault_summary","arguments":{}}}'

# MCP create_inbox_note: the smoke-test note used by the append/mode/ownership
# checks below. Structured export, not raw content — create_inbox_note has
# taken only `title`/`export` since docs/adr/0010-*.md removed REST's
# raw-Markdown create path.
curl -fsS -X POST "$BASE/mcp/" -H "Authorization: Bearer $API_TOKEN" \
     -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
     -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"create_inbox_note","arguments":{"title":"Gateway smoke test","export":{"tldr":["OMV checklist smoke test."]}}}}'

# MCP append_inbox_note: append to the note just created above.
curl -fsS -X POST "$BASE/mcp/" -H "Authorization: Bearer $API_TOKEN" \
     -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
     -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"append_inbox_note","arguments":{"path":"00_Inbox/ChatGPT/Gateway smoke test.md","content":"\nAppended by the OMV checklist.\n"}}}'

# MCP create_inbox_note: a second, independent minimal structured export,
# mode defaults to "summary"
curl -fsS -X POST "$BASE/mcp/" -H "Authorization: Bearer $API_TOKEN" \
     -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
     -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"create_inbox_note","arguments":{"title":"MCP structured export check","export":{"tldr":["OMV checklist smoke test."]}}}}'

# MCP find_duplicate_candidates: run this with the same title as the note
# just created above ("MCP structured export check") and confirm it comes
# back with exactly one candidate, confidence "high", and
# recommendation "confirm" — then with an unrelated title and confirm
# candidates is empty with recommendation "create".
curl -fsS -X POST "$BASE/mcp/" -H "Authorization: Bearer $API_TOKEN" \
     -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
     -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"find_duplicate_candidates","arguments":{"title":"MCP structured export check"}}}'

# MCP create_inbox_note with related_notes: substitute $REAL_NOTE_PATH with a
# real vault-relative .md path from a prior search_notes result. The response
# should show related_notes_linked=1, related_notes_skipped=1 (the invalid
# entry is dropped, not blocking the write), and the created note's
# "## 関連ノート" section should contain exactly one wikilink, not two.
curl -fsS -X POST "$BASE/mcp/" -H "Authorization: Bearer $API_TOKEN" \
     -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
     -d "$(jq -n --arg real "$REAL_NOTE_PATH" \
          '{jsonrpc:"2.0",id:1,method:"tools/call",
            params:{name:"create_inbox_note",arguments:{title:"MCP related notes check",
              export:{tldr:["OMV checklist related-notes smoke test."],
                      related_notes:[$real,"Knowledge/does-not-exist.md"]}}}}')"
```

Walk the vault tree a level at a time to confirm pagination works end to
end: call `get_vault_tree` with a small `limit`, follow `next_cursor` until
it comes back null, and confirm every entry was seen exactly once. Repeat
for `search_notes` with `$QUERY` and a `limit` smaller than its match count.

For the `related_notes` check above: open the created note in Obsidian and
confirm the wikilink resolves (not shown as unresolved/red) and that the note
appears in the linked note's backlinks pane.

**Every `create_inbox_note` call above creates a real note in
`00_Inbox/ChatGPT` on your vault; `append_inbox_note` appends to one.**
There is no delete endpoint or tool (by design — see Security invariants
above), so the Gateway itself cannot remove it. After confirming it synced
correctly, delete the note manually, from Obsidian or directly on the OMV
host. Do the same for any note created while checking LiveSync below.

Also confirm `append_inbox_note` rejects a path outside `00_Inbox/ChatGPT`
(e.g. `Knowledge/...`) with `PATH_OUTSIDE_VAULT`.

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

**Create mode policy**: a newly created note (`create_inbox_note`) is always
written with mode `0o644` — matching the mode an ordinary note in the vault
has — regardless of the container process's umask. An appended-to note
instead keeps whatever mode it already had before the append (see below);
append never changes a note's mode.

**Append and ownership** (`docs/adr/0003-allow-os-replace-for-inbox-append.md`):
`append_inbox_note` uses `os.replace()`, which preserves the note's file
mode but not its owning UID/GID — the appended note's owner becomes whoever
the container process runs as. Before relying on append in production,
confirm on the OMV host:

```bash
ls -ln /path/to/vault/00_Inbox/ChatGPT/'Gateway smoke test.md'   # before append
# ... run the MCP append_inbox_note call above ...
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

Client checks: MCP Inspector connects and lists all eight tools; ChatGPT
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
covers: HTTPS-only, only `/mcp` (the sole functional interface) and
`/api/v1/health` (diagnostics only — docs/adr/0010-*.md) served on this host
name, a request-size cap matching `MAX_REQUEST_BYTES`'s default (2 MiB) — a
hardcoded `request_body { max_size 2MiB }`, not read from the container's
environment, so update it too if `MAX_REQUEST_BYTES` is ever overridden
away from its default — and access logging. Bearer token
checking stays inside the application. `MCP_ALLOWED_HOSTS` on the container
must include this host name, or the MCP transport's own DNS-rebinding
protection rejects every request Caddy forwards.

## Known gaps (tracked for later phases)

- Phase 2's OMV/LiveSync verification checklist above — including the append
  ownership check — has been run on real hardware. What remains is the
  container memory limit (`mem_limit: 512m`) and the Docker `json-file` log
  rotation (`max-size: 10m`, `max-file: 3`), which were added afterwards and
  have not yet been checked against the latest deployed image. Large-vault
  performance work (`docs/IMPLEMENTATION_PLAN.md` section 18's
  "大規模Vault向け改善", e.g. a SQLite FTS5 index) is explicitly out of
  Phase 2's scope.
- Rate limiting, metrics, monitoring, 401-spike detection, a documented SDK
  upgrade procedure, and MCP compatibility testing across SDK updates —
  Phase 3, still open. (The vault-scan concurrency limit, MCP tool-usage
  logging, and `find_duplicate_candidates` — also Phase 3 — have already
  shipped; see `docs/IMPLEMENTATION_PLAN.md` section 18.)
- Vault audit (orphan notes, broken links, stale Inbox notes) — Phase 4.
- MCP resources, prompts, OAuth, a public/tunnelled deployment — out of scope
  for the foreseeable future; see ADR-0001's "Review conditions".
