"""Correlation-id middleware for LiteMCP (M0-BE-003).

Every management and gateway request is assigned a propagatable
``request_id``/``correlation_id``:

1. Ingress validation (docs/architecture/07-observability.md L75): an
   inbound ``X-Request-ID`` is accepted only when it matches
   ``[A-Za-z0-9._-]+`` and is between 1 and 128 bytes long. An absent or
   invalid value is never forwarded: a fresh, valid request_id is generated
   instead.
2. Context propagation (docs/architecture/07-observability.md L212-213,
   L227): the active request_id is exposed to request handling code via
   ``request.state.request_id``.
3. Response echo (docs/architecture/05-agent-gateway.md L408): every response
   carries the request_id back as ``X-Request-Id``.
"""

from __future__ import annotations

import re
import secrets

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

_REQUEST_ID_HEADER = "X-Request-Id"
_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")
_MAX_REQUEST_ID_BYTES = 128


def _is_valid_request_id(value: str) -> bool:
    """Return True when ``value`` is a valid request_id (docs/07 L75).

    A request_id is valid when it matches ``[A-Za-z0-9._-]+`` and is between
    1 and 128 bytes long.
    """
    return bool(_REQUEST_ID_PATTERN.fullmatch(value)) and (
        1 <= len(value.encode("utf-8")) <= _MAX_REQUEST_ID_BYTES
    )


def generate_request_id() -> str:
    """Return a fresh, valid request_id independent of any inbound value.

    The format is ``req_`` followed by 32 hex characters, which satisfies
    ``[A-Za-z0-9._-]+`` within the 1-128 byte range.
    """
    return f"req_{secrets.token_hex(16)}"


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """Assign, propagate, and echo a per-request request_id.

    An inbound ``X-Request-ID`` is accepted only when it is a valid
    request_id; an absent or invalid value triggers generation of a fresh
    valid one. The active request_id is exposed to handling code via
    ``request.state.request_id`` and echoed on every response as
    ``X-Request-Id``.
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        inbound = request.headers.get("x-request-id")
        if inbound is not None and _is_valid_request_id(inbound):
            request_id = inbound
        else:
            request_id = generate_request_id()

        request.state.request_id = request_id
        response = await call_next(request)
        response.headers[_REQUEST_ID_HEADER] = request_id
        return response
