"""Bearer token comparison shared by MCP's authentication middleware.

REST has no bearer-gated route any more (docs/adr/0010-*.md: it is
health-only, and health takes no token) — :func:`verify_bearer_token` now
exists purely for ``app/mcp_auth.py``'s ``McpBearerAuthMiddleware`` to call,
rather than re-implementing ``compare_digest`` against a second copy of the
token. Kept as its own module (rather than folded into app/mcp_auth.py)
since it is transport-neutral and a future REST route could need it again.
"""

from __future__ import annotations

import secrets


def verify_bearer_token(*, provided: str, expected: str) -> bool:
    """Constant-time comparison of a caller-supplied token against the configured one.

    Both sides are encoded to bytes first: ``secrets.compare_digest`` raises
    ``TypeError`` when given two ``str`` operands that aren't both ASCII, and a
    caller can send a non-ASCII bearer token.
    """
    return secrets.compare_digest(provided.encode("utf-8"), expected.encode("utf-8"))
