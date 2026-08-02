"""Error taxonomy.

Every failure the API reports goes through :class:`GatewayError` so the response
body always has the shape defined in docs/IMPLEMENTATION_PLAN.md section 13::

    {"error": {"code": "NOTE_NOT_FOUND", "message": "..."}}

``message`` is client-facing and must never contain an absolute host path, a
token, or note content. Anything useful for debugging goes in ``log_detail``,
which is written to the server log and never serialised into a response.
"""

from __future__ import annotations

from enum import StrEnum


class ErrorCode(StrEnum):
    """The error code vocabulary (IMPLEMENTATION_PLAN section 13).

    Two codes need a note:

    * ``RATE_LIMITED`` is part of the published contract but is never raised in
      Phase 1 — no rate limiting is implemented yet.
    * ``VALIDATION_ERROR`` is an addition to section 13's list (which the plan
      calls "主なエラーコード", i.e. non-exhaustive). Request validation failures
      that are not about a path or a title — a non-integer ``limit``, an unknown
      body field — need a code of their own rather than being forced into
      ``INVALID_PATH``.
    """

    UNAUTHORIZED = "UNAUTHORIZED"
    INVALID_PATH = "INVALID_PATH"
    PATH_OUTSIDE_VAULT = "PATH_OUTSIDE_VAULT"
    NOTE_NOT_FOUND = "NOTE_NOT_FOUND"
    INVALID_FILE_TYPE = "INVALID_FILE_TYPE"
    FILE_TOO_LARGE = "FILE_TOO_LARGE"
    INVALID_TITLE = "INVALID_TITLE"
    NOTE_ALREADY_EXISTS = "NOTE_ALREADY_EXISTS"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    RATE_LIMITED = "RATE_LIMITED"
    INVALID_CURSOR = "INVALID_CURSOR"
    NOTE_MODIFIED = "NOTE_MODIFIED"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class GatewayError(Exception):
    """Base class for every client-visible failure."""

    code: ErrorCode = ErrorCode.INTERNAL_ERROR
    status_code: int = 500
    default_message: str = "An internal error occurred."

    def __init__(self, message: str | None = None, *, log_detail: str | None = None) -> None:
        self.message = message or self.default_message
        self.log_detail = log_detail
        super().__init__(self.message)


class UnauthorizedError(GatewayError):
    code = ErrorCode.UNAUTHORIZED
    status_code = 401
    default_message = "A valid bearer token is required."


class InvalidPathError(GatewayError):
    code = ErrorCode.INVALID_PATH
    status_code = 400
    default_message = "The requested path is not a valid vault-relative note path."


class PathOutsideVaultError(GatewayError):
    code = ErrorCode.PATH_OUTSIDE_VAULT
    status_code = 403
    default_message = "The requested path resolves outside the vault."


class NoteNotFoundError(GatewayError):
    code = ErrorCode.NOTE_NOT_FOUND
    status_code = 404
    default_message = "The requested note was not found."


class InvalidFileTypeError(GatewayError):
    code = ErrorCode.INVALID_FILE_TYPE
    status_code = 400
    default_message = "Only Markdown (.md) notes can be accessed."


class FileTooLargeError(GatewayError):
    code = ErrorCode.FILE_TOO_LARGE
    status_code = 413
    default_message = "The request body is too large."


class InvalidTitleError(GatewayError):
    code = ErrorCode.INVALID_TITLE
    status_code = 400
    default_message = "The title could not be turned into a usable file name."


class NoteAlreadyExistsError(GatewayError):
    code = ErrorCode.NOTE_ALREADY_EXISTS
    status_code = 409
    default_message = "A note with this title already exists and could not be de-duplicated."


class ValidationError(GatewayError):
    code = ErrorCode.VALIDATION_ERROR
    status_code = 400
    default_message = "The request could not be validated."


class InvalidCursorError(GatewayError):
    code = ErrorCode.INVALID_CURSOR
    status_code = 400
    default_message = "The pagination cursor is not valid for this request."


class NoteModifiedError(GatewayError):
    """Raised when a note changes between validation and the atomic write (Phase 2 append)."""

    code = ErrorCode.NOTE_MODIFIED
    status_code = 409
    default_message = "The note changed while the append was being prepared. Retry."


class InternalError(GatewayError):
    """Explicit 500 for conditions we detect rather than crash on."""


def error_envelope(code: ErrorCode, message: str) -> dict:
    """The one response body shape used by every failing response (section 13)."""
    return {"error": {"code": code.value, "message": message}}
