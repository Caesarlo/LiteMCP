"""CSRF and Origin checks for cookie-backed auth writes (M2-WEB-001).

Cookie-backed POSTs (login, refresh, logout) must pass Origin/Referer
allowlisting, reject ``Sec-Fetch-Site: cross-site``, and require
``X-LiteMCP-Request: 1``. There is no synchronizer token in this slice.

Allowed Origins are resolved at request time via ``litemcp.main.csrf_allowed_origins``
so tests can monkeypatch the seam.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from fastapi import HTTPException, Request, status

REASON_ORIGIN_DENIED = "origin_denied"
REASON_CSRF_HEADER = "csrf_header"
REASON_FETCH_METADATA = "fetch_metadata"

CSRF_HEADER = "x-litemcp-request"
CSRF_HEADER_VALUE = "1"


def _forbidden(reason: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={"reason": reason},
    )


def _origin_from_referer(referer: str) -> str | None:
    parsed = urlsplit(referer)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return f"{parsed.scheme}://{parsed.netloc}"


def _effective_origin(request: Request) -> str | None:
    """Return the request Origin, falling back to Referer when Origin is absent."""
    origin = request.headers.get("origin")
    if origin:
        return origin
    referer = request.headers.get("referer")
    if not referer:
        return None
    return _origin_from_referer(referer)


def require_cookie_write_csrf(request: Request) -> None:
    """Reject cookie-backed writes that fail CSRF / Origin / Fetch-Metadata checks."""
    import litemcp.main as main_module

    allowed = main_module.csrf_allowed_origins()
    candidate = _effective_origin(request)
    if candidate is None or candidate not in allowed:
        raise _forbidden(REASON_ORIGIN_DENIED)

    fetch_site = (request.headers.get("sec-fetch-site") or "").lower()
    if fetch_site == "cross-site":
        raise _forbidden(REASON_FETCH_METADATA)

    if request.headers.get(CSRF_HEADER) != CSRF_HEADER_VALUE:
        raise _forbidden(REASON_CSRF_HEADER)
