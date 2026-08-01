# Repository purpose

Build a secure REST and MCP gateway for controlled access to an
Obsidian Vault.

# Security invariants

- The complete Vault is read-only.
- Only `00_Inbox/ChatGPT` is writable.
- Never implement delete, arbitrary move, arbitrary rename, shell execution,
  Git execution, CouchDB access, or writes to `.obsidian`.
- Reject path traversal, absolute paths, hidden paths, and symbolic links.
- Never expose absolute host paths, tokens, or note contents in logs.

# Development workflow

- Implement only the explicitly requested phase from
  `docs/IMPLEMENTATION_PLAN.md`.
- Keep changes small and reviewable.
- Add or update tests with every behavioral change.
- Run the relevant test suite before reporting completion.
- Update README and OpenAPI documentation when behavior changes.
- Report changed files, test results, assumptions, and unresolved issues.

# Repository safety

- Do not use `git reset --hard`, `git clean`, force push, or destructive Git
  operations.
- Do not modify an actual Obsidian Vault during automated tests.
- Use only test fixtures for development and tests.
- Never commit `.env`, credentials, tokens, private keys, or production paths.

# Initial commands

- Test: `pytest`
- Lint: `ruff check .`
- Compose validation: `docker compose config`
