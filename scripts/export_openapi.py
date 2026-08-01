#!/usr/bin/env python3
"""Export app.main.rest_app's OpenAPI schema to openapi.json.

Run after any change to routers/models/exceptions that affects the schema:

    .venv/bin/python scripts/export_openapi.py          # write openapi.json
    .venv/bin/python scripts/export_openapi.py --check   # CI: fail on drift

Reads ``rest_app`` — the FastAPI application itself — rather than
``app.main.app``: since Phase 1.5, ``app`` is a plain Starlette instance that
mounts ``rest_app`` alongside the MCP transport, and only a FastAPI instance
has ``.openapi()``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_PATH = REPO_ROOT / "openapi.json"


def _build_schema() -> dict:
    import os

    # Settings just need to construct successfully; export never touches the
    # filesystem paths it names.
    os.environ.setdefault("API_TOKEN", "openapi-export-placeholder-token")
    os.environ.setdefault("MCP_ALLOWED_HOSTS", "localhost")

    sys.path.insert(0, str(REPO_ROOT))
    from app.main import rest_app

    return rest_app.openapi()


def main() -> int:
    check_only = "--check" in sys.argv
    schema = _build_schema()
    rendered = json.dumps(schema, indent=2, ensure_ascii=False, sort_keys=True) + "\n"

    if check_only:
        current = OUTPUT_PATH.read_text(encoding="utf-8") if OUTPUT_PATH.exists() else ""
        if current != rendered:
            print(f"{OUTPUT_PATH} is stale; run scripts/export_openapi.py", file=sys.stderr)
            return 1
        print(f"{OUTPUT_PATH} is up to date.")
        return 0

    OUTPUT_PATH.write_text(rendered, encoding="utf-8")
    print(f"Wrote {OUTPUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
