"""Per-route OpenAPI ``responses=`` — the REST error contract (IMPLEMENTATION_PLAN section 13).

Before this module, no router declared a ``responses=`` for anything but the
success case. Two consequences followed, both wrong:

1. FastAPI synthesised a ``422`` response backed by its own
   ``HTTPValidationError``/``ValidationError`` schemas — a shape this API
   never actually returns. ``app/main.py``'s ``handle_validation_error``
   converts every ``RequestValidationError`` into a **400** with the same
   :class:`~app.models.ErrorResponse` envelope every other failure uses.
2. Every real failure shape — 400/401/403/404/409/413/503/500, each with a
   real ``error.code`` — was entirely undocumented.

:func:`error_responses` fixes both: it builds a route's ``responses=`` from
the actual :class:`~app.exceptions.ErrorCode` values that operation can
raise, and always adds a ``"default"`` entry pointing at the same
:class:`~app.models.ErrorResponse` model. That ``"default"`` entry is what
suppresses (1) — FastAPI only injects its own ``422`` when none of
``"422"``/``"4XX"``/``"default"`` is already present in
``operation["responses"]`` (verified against the installed version:
``fastapi/openapi/utils.py``'s ``get_openapi_path``) — and it is also the
accurate contract for (2): every non-2xx response, on every operation, is
this one envelope. ``app/main.py`` has no exception handler that produces
anything else.
"""

from __future__ import annotations

from app.exceptions import ErrorCode
from app.models import ErrorResponse

_DEFAULT_DESCRIPTION = (
    "Every failing response — including any status code not listed "
    "individually above — uses this same envelope; only `error.code` and "
    "`error.message` vary."
)


def error_responses(mapping: dict[int, tuple[ErrorCode, ...]]) -> dict[int | str, dict]:
    """Build one route's ``responses=`` for the REST error contract.

    ``mapping`` lists, for each status code *this operation* can actually
    return, the ``ErrorCode`` values it can carry — the set reachable from
    this specific route, not every ``ErrorCode`` that ever maps to that
    status somewhere in the app (which would document failures this
    operation can never produce, e.g. ``INVALID_FILE_TYPE`` on a route that
    never resolves a note path at all).
    """
    responses: dict[int | str, dict] = {
        status: {
            "model": ErrorResponse,
            "description": (
                "`error.code` is one of: " + ", ".join(code.value for code in codes) + "."
            ),
        }
        for status, codes in mapping.items()
    }
    responses["default"] = {"model": ErrorResponse, "description": _DEFAULT_DESCRIPTION}
    return responses
