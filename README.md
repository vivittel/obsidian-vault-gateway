# Obsidian Vault Gateway

Read-mostly REST gateway over an Obsidian vault, built for ChatGPT Actions:
full-vault search, note reads, and note creation restricted to one directory.

This is **Phase 1** of `docs/IMPLEMENTATION_PLAN.md` — see `docs/PHASE1_PLAN.md`
for the detailed design and the points where this implementation deviates from
that plan (each one justified). Phases 2–4 (directory tree / vault summary /
append-to-note / vault audit / ChatGPT Actions registration) are not
implemented yet.

## Security invariants

These hold regardless of what future phases add (see `AGENTS.md`):

- The whole vault is mounted **read-only**.
- Only `00_Inbox/ChatGPT` is writable, and only through `POST /api/v1/inbox/notes`.
- There is no delete, move, rename, or arbitrary-path write endpoint.
- Every path is validated against traversal, absolute paths, hidden files, and
  symlinks before touching the filesystem (`app/services/path_security.py`).
- Responses and logs never contain an absolute host path, a bearer token, or
  note content — only vault-relative paths.

## Endpoints

| Method | Path | operationId | Auth |
|---|---|---|---|
| GET | `/api/v1/health` | `getHealth` | none |
| GET | `/api/v1/search` | `searchNotes` | Bearer |
| GET | `/api/v1/notes` | `readNote` | Bearer |
| POST | `/api/v1/inbox/notes` | `createInboxNote` | Bearer |

Full schema: `openapi.json` (regenerate with `scripts/export_openapi.py`
after changing any router/model), or `GET /docs` on a running instance.

`GET /api/v1/notes` takes the note path as a **query parameter**
(`?path=Knowledge/PC/GPU/RTX 5070.md`), not as part of the URL path — see
`docs/PHASE1_PLAN.md` section 4.5 for why.

## Configuration

Copy `.env.example` to `.env` and fill it in:

```bash
cp .env.example .env
openssl rand -hex 32   # use the output as API_TOKEN
```

See `.env.example` for every variable. The two you must set for a real
deployment are `API_TOKEN` and `VAULT_HOST_PATH` / `INBOX_HOST_PATH`.

## Running locally (no Docker)

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'

API_TOKEN=$(openssl rand -hex 32) \
VAULT_READ_ROOT=/path/to/a/test/vault \
VAULT_INBOX_ROOT=/path/to/a/test/vault/00_Inbox/ChatGPT \
  .venv/bin/uvicorn app.main:app --reload
```

**Never point `VAULT_READ_ROOT` at a real vault for anything other than
manual, careful checking.** Automated tests never do this — see Testing below.

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

## Docker / Compose

```bash
docker compose config    # validate before starting anything
docker compose up -d --build
```

The image is a non-root, `read_only: true` container with all Linux
capabilities dropped (see `Dockerfile` / `compose.yaml`). The vault is never
copied into the image — it is bind-mounted at runtime.

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

docker compose up -d --build

BASE=https://obsidian-api.example.com/api/v1

curl -fsS "$BASE/health"

curl -fsS -H "Authorization: Bearer $API_TOKEN" --get "$BASE/search" \
     --data-urlencode 'q=RTX 5070' --data-urlencode 'limit=5'

curl -fsS -H "Authorization: Bearer $API_TOKEN" --get "$BASE/notes" \
     --data-urlencode 'path=Knowledge/PC/GPU/RTX 5070.md'

curl -fsS -X POST -H "Authorization: Bearer $API_TOKEN" -H 'Content-Type: application/json' \
     -d '{"title":"Gateway smoke test","content":"# Gateway smoke test\n"}' \
     "$BASE/inbox/notes"
```

**The `POST` above creates a real note in `00_Inbox/ChatGPT` on your vault.**
Phase 1 has no delete endpoint (by design — see Security invariants above), so
the gateway itself cannot remove it. After confirming it synced correctly,
delete `00_Inbox/ChatGPT/Gateway smoke test.md` manually, from Obsidian or
directly on the OMV host. Do the same for any note created while checking
LiveSync below.

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

LiveSync check — with the container running, use the smoke-test `POST` above
(or the ChatGPT-facing flow once Phase 3 wires it up) and confirm, in order:

1. The note appears on the server's vault filesystem.
2. `livesync-cli` detects the change.
3. It syncs to CouchDB.
4. It appears in Obsidian on a PC.
5. It appears in Obsidian on an iPhone.

Then delete the test note as noted above.

## Caddy

Example site block: `docs/caddy/obsidian-api.Caddyfile`. Requirements it
covers: HTTPS-only, only `/api/v1/*` served on this host name, a request-size
cap, and access logging. Bearer token checking stays inside the application.

## Known gaps (tracked for later phases)

- `GET /api/v1/vault/tree`, `GET /api/v1/vault/summary` — Phase 2.
- `POST /api/v1/inbox/notes/{note_id}/append` — Phase 2.
- Search pagination (`cursor`) — the response always has `next_cursor: null`.
- ChatGPT Actions registration and Custom-GPT-facing descriptions — Phase 3.
- Vault audit (orphan notes, broken links, stale Inbox notes) — Phase 4.
