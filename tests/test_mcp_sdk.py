"""MCP_IMPLEMENTATION_PLAN section 5: pin the MCP Python SDK and record the
exact API surface Phase 1.5 is built against.

The plan's own concept code (section 9) targets the SDK's v1 line. By the time
this repo implemented Phase 1.5, PyPI's *stable* release was already 2.0.0 (see
docs/adr/0002-use-mcp-python-sdk-v2.md) — v1.x had moved to a maintenance-only
line. These tests pin the exact API shapes (class names, field spellings,
defaults) that later slices are written against, so an accidental upgrade that
renames or reshapes any of them fails here first instead of inside an MCP
protocol test.
"""

from __future__ import annotations

import importlib.metadata


def test_mcp_sdk_version_is_pinned() -> None:
    assert importlib.metadata.version("mcp") == "2.0.0"


def test_mcp_server_importable_from_documented_module() -> None:
    from mcp.server.mcpserver import MCPServer

    assert MCPServer.__name__ == "MCPServer"


def test_mcp_error_importable_from_shared_exceptions() -> None:
    from mcp.shared.exceptions import MCPError

    assert issubclass(MCPError, Exception)


def test_tool_annotations_importable_from_mcp_types_not_mcp_types_package() -> None:
    """MCP_IMPLEMENTATION_PLAN feedback: import from ``mcp.types``, not the
    standalone ``mcp_types`` package, even though v2 makes the latter the
    canonical home. ``mcp.types`` is a verified wildcard mirror (same objects).
    """
    from mcp.types import ToolAnnotations

    fields = set(ToolAnnotations.model_fields)
    assert fields == {
        "title",
        "read_only_hint",
        "destructive_hint",
        "idempotent_hint",
        "open_world_hint",
    }


def test_high_level_client_defaults_to_auto_mode() -> None:
    from mcp.client.client import Client

    mode_field = Client.__dataclass_fields__["mode"]
    assert mode_field.default == "auto"


def test_default_max_request_body_size_is_4mib() -> None:
    """Recorded so MCP_IMPLEMENTATION_PLAN section 21's 2 MiB choice (shared
    with MAX_REQUEST_BYTES, see U3) is a deliberate tightening of the SDK
    default, not an accident.
    """
    from mcp.server.mcpserver.server import DEFAULT_MAX_REQUEST_BODY_SIZE

    assert DEFAULT_MAX_REQUEST_BODY_SIZE == 4 * 1024 * 1024


def test_modern_protocol_version_is_2026_07_28() -> None:
    from mcp.server.lowlevel.server import MODERN_PROTOCOL_VERSIONS

    assert MODERN_PROTOCOL_VERSIONS == ("2026-07-28",)


def test_transport_security_settings_fields() -> None:
    from mcp.server.transport_security import TransportSecuritySettings

    assert set(TransportSecuritySettings.model_fields) == {
        "enable_dns_rebinding_protection",
        "allowed_hosts",
        "allowed_origins",
    }
