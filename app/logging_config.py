"""The one place logging is configured (IMPLEMENTATION_PLAN section 14,
MCP_IMPLEMENTATION_PLAN section 16).

Before this module existed, nothing in the application configured logging at
all — and the resulting output was unusable in production::

    request
    Terminating session: None
    mcp_call
    mcp_auth_failed

Every field ``app/middleware.py``, ``app/mcp_server.py`` and
``app/mcp_auth.py`` carefully attach via ``extra={...}`` was being discarded,
including the timestamp section 14 lists first among the things that must be
recorded. The cause was a side effect: constructing an ``MCPServer`` calls the
SDK's own ``configure_logging()``, which calls
``logging.basicConfig(level=..., format="%(message)s")`` (verified against the
installed SDK at ``mcp/server/mcpserver/server.py`` →
``mcp/server/mcpserver/utilities/logging.py``). With no configuration of our
own, that bare ``%(message)s`` root handler was what rendered every line.

Three consequences shape this module:

1. :func:`configure_logging` must run **before** ``app.mcp_server`` constructs
   its ``MCPServer``. ``logging.basicConfig`` is documented to do nothing once
   the root logger has a handler, so configuring first turns the SDK's call
   into a no-op — which is why the call site is at the top of
   ``app/mcp_server.py``, immediately above the construction it has to
   precede, rather than in ``app/main.py`` where the ordering would be
   invisible.
2. It takes **no** :class:`~app.config.Settings`, and reads ``TZ`` and
   ``LOG_LEVEL`` from the environment itself. Depending on ``Settings`` would
   be a bootstrap cycle: ``Settings()`` can fail validation (a missing
   ``API_TOKEN``, an empty ``MCP_ALLOWED_HOSTS``), and that failure is exactly
   the kind of thing that should be logged in the normal format rather than
   crashing before logging exists. It also has to stay importable at
   collection time in tests, which construct no ``Settings`` at all. The two
   defaults below therefore mirror ``Settings.tz`` and ``Settings.log_level``.
3. It adds its handler to the root logger instead of replacing the handler
   list (as ``logging.config.dictConfig`` with an explicit ``root`` would).
   Replacing would delete pytest's ``caplog`` handler for whichever test first
   imports the application, silently breaking the record-level assertions in
   tests/test_logging.py. Adding is also what keeps this function safe to call
   more than once: it removes only the handler a previous call of its own
   installed, identified by name.

Format is aligned plain text rather than JSON because the logs are read in
Portainer's and OMV's plain-text log viewers, the field set is fixed by
sections 14 and 16, and — decisively — the MCP access log contains no
caller-controlled free text at all (``transport``, ``method``, ``tool``,
``status``, ``reason`` and ``code`` are closed vocabularies; ``duration_ms``
and ``result_count`` are numbers; section 16's U1 keeps ``note_path`` out of
MCP logs entirely). The log-injection and split-line risks that would argue
for JSON's structural escaping therefore do not arise on the primary
transport. :class:`PlainLogFormatter` still escapes newlines itself — see
:func:`_escape` — because an unstructured record's own message (e.g. an
exception traceback, or anything a future field puts through this
formatter's tail) is free text and can legitimately contain one. Swapping
in a JSON formatter later means adding one class here and changing which
one :func:`configure_logging` installs.
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# Handlers are looked up by name so configure_logging() can replace its own
# previous handler without touching anyone else's (see module docstring).
_HANDLER_NAME = "obsidian_gateway_stdout"

# Mirrors Settings.tz / Settings.log_level. Duplicated deliberately: see point
# 2 of the module docstring.
_DEFAULT_TZ = "Asia/Tokyo"
_DEFAULT_LEVEL = "INFO"

# Only these ``extra`` keys are ever rendered in the trailing key=value part.
# An allow-list, not a deny-list: AGENTS.md forbids note content, bearer tokens
# and absolute host paths in logs, and a deny-list would silently open a new
# leak the first time someone adds an unrelated ``extra`` field. Anything not
# named here is dropped.
#
# Order matters: values that can contain spaces (``note``, ``detail``) come
# last so the fixed columns before them stay at stable ``awk`` field positions.
_TAIL_FIELDS: tuple[tuple[str, str], ...] = (
    ("query_length", "q_len"),
    ("result_count", "results"),
    ("reason", "reason"),
    ("code", "code"),
    ("note_path", "note"),
    ("detail", "detail"),
)

# WARNING/CRITICAL are abbreviated so the level column stays 5 wide.
_LEVEL_NAMES = {"WARNING": "WARN", "CRITICAL": "CRIT"}

_TS_WIDTH = 28
_LEVEL_WIDTH = 5
_SOURCE_WIDTH = 7
_METHOD_WIDTH = 10
_TARGET_WIDTH = 26
_STATUS_WIDTH = 12
_DURATION_WIDTH = 8

_MISSING = "-"


def _escape(value: object) -> str:
    """Render a value on a single line.

    One log record must be one log line: a value containing a newline would
    otherwise split into two lines and read as two independent events. Applied
    to every allow-listed tail value, not just free text (an unstructured
    record's own message, an exception traceback): the ``note_path``/``note``
    entry below is kept for exactly this reason even though no current caller
    populates it (REST is health-only now and MCP's U1 keeps note paths out
    of its own logs entirely — see this module's docstring) — a note path is
    derived from a real filename, and a newline in a filename is legal on
    Linux even though app/services/path_security.py rejects null bytes and
    backslashes, so this is not purely theoretical.
    """
    text = str(value).replace("\\", "\\\\")
    return text.replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")


class PlainLogFormatter(logging.Formatter):
    """Aligned plain text: fixed columns, then ``key=value`` for optional fields.

    Records carrying a ``transport`` extra (the REST and MCP access logs) get
    the full column layout — ``$1`` timestamp, ``$2`` level, ``$3`` source,
    ``$4`` method, ``$5`` target, ``$6`` status, ``$7`` duration, then the
    tail::

        2026-08-02T21:14:03.412+0900  DEBUG rest    GET        /api/v1/health
        2026-08-02T21:14:07.883+0900  INFO  mcp     tools/call search_notes

    Everything else — uvicorn's startup lines, the MCP SDK's own messages, and
    the application's error records — shares only the first three columns and
    then carries its message as free text::

        2026-08-02T21:13:58.001+0900  INFO  uvicorn Started server process [1]

    Every column is exactly one whitespace-separated field, which is what makes
    ``awk '$3=="mcp" {print $5}'`` a reliable way to select application MCP
    lines and read the tool off them. Only the tail can contain spaces, and the
    fields there that can (``note``, ``detail``) come last.
    """

    def __init__(self, tz: ZoneInfo) -> None:
        super().__init__()
        self._tz = tz

    def format(self, record: logging.LogRecord) -> str:
        head = f"{self._timestamp(record):<{_TS_WIDTH}}  {self._level(record):<{_LEVEL_WIDTH}} "
        head += f"{self._source(record):<{_SOURCE_WIDTH}}"

        tail = self._tail(record)

        if getattr(record, "transport", None) is None:
            # Unstructured record: message as free text, tail appended if the
            # record happened to carry any allow-listed field (the error logs
            # in app/main.py and app/mcp_server.py carry code/detail).
            body = _escape(record.getMessage())
            return " ".join(part for part in (head, body, tail) if part)

        columns = (
            f"{self._column(record, 'method'):<{_METHOD_WIDTH}}"
            f" {self._target(record):<{_TARGET_WIDTH}}"
            f" {self._column(record, 'status', 'status_code'):<{_STATUS_WIDTH}}"
            f" {self._duration(record):<{_DURATION_WIDTH}}"
        )
        return " ".join(part for part in (head, columns, tail) if part).rstrip()

    def _timestamp(self, record: logging.LogRecord) -> str:
        """ISO 8601 with a ``T`` separator, not a space.

        The space would make the timestamp two whitespace-separated fields and
        shift every column after it by one, defeating the point of aligning
        them: with ``T``, each column is exactly one ``awk`` field.
        """
        moment = datetime.fromtimestamp(record.created, tz=self._tz)
        return f"{moment:%Y-%m-%dT%H:%M:%S}.{moment.microsecond // 1000:03d}{moment:%z}"

    def _level(self, record: logging.LogRecord) -> str:
        return _LEVEL_NAMES.get(record.levelname, record.levelname)

    def _source(self, record: logging.LogRecord) -> str:
        """The ``transport`` when there is one, otherwise a short origin label.

        Keeping application transports (``rest``/``mcp``) and everything else
        (``uvicorn``/``mcp-sdk``/``app``) in the same column is what lets one
        ``awk`` expression separate them.
        """
        transport = getattr(record, "transport", None)
        if transport:
            return _escape(transport)
        if record.name.startswith("uvicorn"):
            return "uvicorn"
        if record.name == "mcp" or record.name.startswith("mcp."):
            return "mcp-sdk"
        if record.name.startswith("obsidian_gateway"):
            return "app"
        return _escape(record.name.split(".", 1)[0])[:_SOURCE_WIDTH]

    def _target(self, record: logging.LogRecord) -> str:
        """What the operation acted on: the health route, an MCP tool, or the event.

        Falling back to the message keeps a record with neither — currently
        only ``mcp_auth_failed``, rejected before any tool is named — from
        rendering as a bare dash.
        """
        for attribute in ("route", "tool"):
            value = getattr(record, attribute, None)
            if value:
                return _escape(value)
        return _escape(record.getMessage())

    def _column(self, record: logging.LogRecord, *attributes: str) -> str:
        for attribute in attributes:
            value = getattr(record, attribute, None)
            if value is not None:
                return _escape(value)
        return _MISSING

    def _duration(self, record: logging.LogRecord) -> str:
        duration_ms = getattr(record, "duration_ms", None)
        return _MISSING if duration_ms is None else f"{duration_ms}ms"

    def _tail(self, record: logging.LogRecord) -> str:
        parts = [
            f"{label}={_escape(value)}"
            for attribute, label in _TAIL_FIELDS
            if (value := getattr(record, attribute, None)) is not None
        ]
        if record.exc_info:
            # Kept on the same line: a multi-line traceback would otherwise
            # read as several unrelated events. This is the one field that can
            # contain a container-internal path, and it only appears for
            # genuinely unexpected exceptions (app/main.py's
            # handle_unexpected_error, app/mcp_server.py's
            # mcp_tool_unhandled_error) — GatewayError takes the sanitised
            # ``detail`` route instead.
            parts.append(f"exc={_escape(self.formatException(record.exc_info))}")
        return " ".join(parts)


def _timezone() -> ZoneInfo:
    """``TZ`` as a :class:`ZoneInfo`, falling back to UTC rather than raising.

    Misconfiguring ``TZ`` must not stop the process from logging — the log line
    reporting the misconfiguration is the one that matters most.
    """
    for name in (os.environ.get("TZ", "").strip(), _DEFAULT_TZ):
        if not name:
            continue
        try:
            return ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError):
            continue
    return ZoneInfo("UTC")


def _level() -> int:
    return logging.getLevelNamesMapping().get(
        os.environ.get("LOG_LEVEL", _DEFAULT_LEVEL).strip().upper(),
        logging.INFO,
    )


def configure_logging() -> None:
    """Install the one stdout handler and set every level this app cares about.

    Safe to call more than once: only the handler a previous call installed is
    removed, so pytest's ``caplog`` handler (and anything else on the root
    logger) survives. Must run before ``app.mcp_server`` constructs its
    ``MCPServer`` — see the module docstring.
    """
    handler = logging.StreamHandler(sys.stdout)
    handler.set_name(_HANDLER_NAME)
    handler.setFormatter(PlainLogFormatter(_timezone()))

    root = logging.getLogger()
    for existing in list(root.handlers):
        if existing.get_name() == _HANDLER_NAME:
            root.removeHandler(existing)
    root.addHandler(handler)

    # WARNING on the root logger, not LOG_LEVEL: this only affects loggers that
    # set no level of their own, so it suppresses unrelated third-party INFO
    # chatter without touching anything configured below. Propagation to root's
    # *handlers* is not gated by root's level, so the DEBUG health-check line
    # still reaches stdout when LOG_LEVEL=DEBUG.
    root.setLevel(logging.WARNING)

    # LOG_LEVEL was previously dead configuration: it reached Settings (and
    # compose.yaml / .env.example set it) but nothing ever applied it, so the
    # SDK's own default level was what actually took effect.
    level = _level()
    for name in ("obsidian_gateway", "obsidian_gateway.access", "obsidian_gateway.mcp"):
        logging.getLogger(name).setLevel(level)

    # uvicorn's own dictConfig gives ``uvicorn`` a handler with
    # ``propagate: False``, so its records never reach the root handler
    # installed above and would keep rendering in uvicorn's "INFO:     ..."
    # format alongside ours. Detaching that handler and letting the records
    # propagate is what puts startup and error lines into the same columns as
    # everything else.
    for name in ("uvicorn", "uvicorn.error"):
        logger = logging.getLogger(name)
        for existing in list(logger.handlers):
            logger.removeHandler(existing)
        logger.propagate = True
        logger.setLevel(logging.INFO)

    # ``uvicorn.access`` is pointedly NOT unified with the rest. Its formatter
    # prints the raw request line — query string included — so letting it
    # propagate here would put the search term straight into the log, which
    # IMPLEMENTATION_PLAN section 14 forbids outright and which is the entire
    # reason the Dockerfile passes --no-access-log. Silencing it here as well
    # means the invariant no longer depends on that CLI flag being present:
    # uvicorn implements --no-access-log by clearing this logger's handlers and
    # setting propagate=False, and doing the same unconditionally makes the
    # guarantee hold even if the flag is ever dropped from the CMD.
    uvicorn_access = logging.getLogger("uvicorn.access")
    for existing in list(uvicorn_access.handlers):
        uvicorn_access.removeHandler(existing)
    uvicorn_access.propagate = False

    # The SDK logs one "Terminating session: None" at INFO per request: this
    # server is stateless (stateless_http=True), so there is never a session id
    # to report and the line carries no information. Silencing only
    # ``mcp.server.streamable_http`` keeps
    # ``mcp.server.streamable_http_manager``'s genuinely useful
    # startup/shutdown lines — the two are siblings under ``mcp.server``, not
    # parent and child.
    logging.getLogger("mcp.server.streamable_http").setLevel(logging.WARNING)
