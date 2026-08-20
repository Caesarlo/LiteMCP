"""Contract tests for CSRF and Origin protection on cookie-backed auth writes.

M2-WEB-001: every management write that depends on Cookies must pass both CSRF
and allowed-Origin checks (docs/architecture/02-admin-auth.md §9.1, §13).

Cookie-backed management writes in this file are only:

    POST /api/v1/auth/login
    POST /api/v1/auth/refresh
    POST /api/v1/auth/logout

Access JWT uses the Authorization Header; ordinary ``/api/v1/admin/*`` APIs
are not Cookie-authenticated and are out of this file's success path.

First version does **not** use a synchronizer CSRF token or signed
double-submit cookie. Defense in depth for these cookie-backed POSTs:

1. Default allow same-Origin only. CORS must not use ``*`` with credentials;
   unknown Origins must not be allowed to send the custom header.
2. Validate ``Origin``. If ``Origin`` is missing, fall back to ``Referer`` per
   an explicit policy; mismatch rejects.
3. For modern browsers, validate ``Sec-Fetch-Site`` and reject ``cross-site``
   state-changing requests.
4. Frontend sends ``X-LiteMCP-Request: 1`` on login, refresh, and logout.
   Missing or wrong values are CSRF failures.
5. State changes only via POST/PUT/PATCH/DELETE — GET must not produce side
   effects on these auth cookie operations.
6. CSRF / Origin / Fetch-Metadata rejection is HTTP **403** (not 401).
   401 is for missing/invalid credentials.
7. Production refresh Cookie is ``__Secure-litemcp_rt`` (Path=/api/v1/auth,
   HttpOnly, Secure, SameSite=Strict). This file does not require a successful
   login/refresh token mint to prove rejection cases.

Passing the CSRF+Origin gate is necessary but not sufficient for a 2xx. Positive
cases assert the request is **not** rejected as CSRF/Origin. The response MAY
still be 400/401/422 because credentials are missing.

Stable, non-secret reason codes declared by this contract (core/errors.py-style
JSON; must not echo Cookie values, Authorization headers, refresh secrets, or
CSRF secrets):

    origin_denied     — Origin/Referer missing, unusable, or not allowlisted
    csrf_header       — missing or wrong ``X-LiteMCP-Request``
    fetch_metadata    — ``Sec-Fetch-Site: cross-site`` on a state-changing request

# ---------------------------------------------------------------------------
# FAILURE-INJECTION / SETTINGS SEAM (the implementer MUST provide this)
#
#     CSRF/Origin checks MUST resolve the allowed Origin list at **request
#     time** so monkeypatching is observable (same pattern as
#     ``probe_database`` / ``probe_redis`` on ``litemcp.main`` in
#     tests/api/test_health.py).
#
#     The ``litemcp.main`` module MUST expose a synchronous lookup:
#
#         csrf_allowed_origins() -> frozenset[str]
#
#     returning exact Origins (scheme + host [+ port]), e.g.
#     ``frozenset({"https://admin.litemcp.test"})``.
#
#     Cookie-backed login/refresh/logout POSTs MUST call this lookup (or an
#     equivalent Settings field read at request time that these tests can
#     monkeypatch on ``litemcp.main`` under the same name). Default production
#     policy is same-Origin only; tests inject an explicit allowlist.
#
#     ``raising=False`` is used so a missing seam still reaches the HTTP
#     assertion (expected RED: CSRF/Origin protection or the auth routes are
#     absent), not an AttributeError during fixture setup.
# ---------------------------------------------------------------------------
"""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

import litemcp.main as main_module
from litemcp.main import app

# Declared allowlist member used by every test in this module.
ALLOWED_ORIGIN = "https://admin.litemcp.test"
CROSS_SITE_ORIGIN = "https://evil.example"

LOGIN_PATH = "/api/v1/auth/login"
REFRESH_PATH = "/api/v1/auth/refresh"
LOGOUT_PATH = "/api/v1/auth/logout"

COOKIE_WRITE_PATHS = (LOGIN_PATH, REFRESH_PATH, LOGOUT_PATH)

CSRF_HEADER = "X-LiteMCP-Request"
CSRF_HEADER_VALUE = "1"

REASON_ORIGIN_DENIED = "origin_denied"
REASON_CSRF_HEADER = "csrf_header"
REASON_FETCH_METADATA = "fetch_metadata"

CSRF_ORIGIN_REASONS = frozenset(
    {REASON_ORIGIN_DENIED, REASON_CSRF_HEADER, REASON_FETCH_METADATA}
)

_FORBIDDEN_SECRET_ECHO = [
    r"authorization\s*:",
    r"bearer\s+[a-z0-9._\-]+",
    r"__secure-litemcp_rt",
    r"litemcp_rt=",
    r"csrf[_-]?secret",
    r"refresh[_-]?token\s*[:=]",
]


def _pin_allowed_origins(monkeypatch: pytest.MonkeyPatch) -> None:
    """Inject the explicit allowlist via the request-time seam on litemcp.main."""
    monkeypatch.setattr(
        main_module,
        "csrf_allowed_origins",
        lambda: frozenset({ALLOWED_ORIGIN}),
        raising=False,
    )


def _client() -> TestClient:
    return TestClient(app)


def _error_payload(response) -> dict:
    try:
        payload = response.json()
    except ValueError as exc:  # pragma: no cover - contract requires JSON errors
        raise AssertionError(
            f"CSRF/Origin error body must be JSON, got {response.text!r}"
        ) from exc
    assert isinstance(payload, dict), f"unified error body must be an object: {payload!r}"
    return payload


def _error_reason(payload: dict) -> str:
    """Read the stable reason/code from the unified error object."""
    reason = payload.get("reason")
    if isinstance(reason, str) and reason:
        return reason
    detail = payload.get("detail")
    if isinstance(detail, dict):
        nested = detail.get("reason") or detail.get("code")
        if isinstance(nested, str) and nested:
            return nested
    code = payload.get("code")
    if isinstance(code, str) and code:
        return code
    raise AssertionError(
        "unified CSRF/Origin error must carry a stable non-empty "
        f"`reason` (or `code` / `detail.reason`): {payload!r}"
    )


def _assert_no_secret_echo(text: str) -> None:
    lowered = text.lower()
    for pattern in _FORBIDDEN_SECRET_ECHO:
        assert not re.search(pattern, lowered), (
            "CSRF/Origin error body must not echo Cookie, Authorization, "
            f"refresh, or CSRF secrets (pattern {pattern!r}): {text!r}"
        )


def _assert_cors_not_wildcard_with_credentials(response) -> None:
    acao = response.headers.get("access-control-allow-origin")
    acac = (response.headers.get("access-control-allow-credentials") or "").lower()
    assert acao != "*", (
        "CORS must not use Access-Control-Allow-Origin: * "
        "(unknown Origins must not send the custom CSRF header)"
    )
    if acac == "true":
        assert acao not in {None, "", "*"}, (
            "CORS must not combine credentialed responses with a wildcard Origin"
        )


def _assert_csrf_origin_403(response, expected_reason: str) -> None:
    assert response.status_code == 403, (
        "CSRF/Origin/Fetch-Metadata rejection must be HTTP 403, not 401 "
        f"(got {response.status_code}): {response.text!r}"
    )
    payload = _error_payload(response)
    reason = _error_reason(payload)
    assert reason == expected_reason, (
        f"expected stable reason {expected_reason!r}, got {reason!r} from {payload!r}"
    )
    assert reason in CSRF_ORIGIN_REASONS
    _assert_no_secret_echo(response.text)
    _assert_cors_not_wildcard_with_credentials(response)


def _assert_not_csrf_origin_reject(response) -> None:
    """Gate passed: must not be 403 with a CSRF/Origin/Fetch-Metadata reason.

    400/401/422 (or other non-CSRF outcomes) are allowed because credentials
    may be missing. A 403 is only acceptable if its reason is *not* one of
    the CSRF/Origin codes declared above (e.g. later RBAC) — the CSRF gate
    itself must have passed.
    """
    if response.status_code != 403:
        return
    payload = _error_payload(response)
    reason = _error_reason(payload)
    assert reason not in CSRF_ORIGIN_REASONS, (
        "request matched the CSRF/Origin allow path but was rejected as "
        f"CSRF/Origin ({reason!r}): {payload!r}"
    )


class TestCsrfOriginRejection:
    """Negative cases: cookie-backed POSTs without a valid CSRF/Origin gate."""

    @pytest.mark.parametrize("path", COOKIE_WRITE_PATHS)
    def test_post_missing_origin_and_referer_is_403(
        self, monkeypatch: pytest.MonkeyPatch, path: str
    ) -> None:
        _pin_allowed_origins(monkeypatch)
        response = _client().post(
            path,
            headers={
                CSRF_HEADER: CSRF_HEADER_VALUE,
                # Sec-Fetch-Site omitted: treat as non-modern-browser path;
                # Origin/Referer policy still applies.
            },
        )
        _assert_csrf_origin_403(response, REASON_ORIGIN_DENIED)

    @pytest.mark.parametrize("path", COOKIE_WRITE_PATHS)
    def test_post_disallowed_origin_is_403(
        self, monkeypatch: pytest.MonkeyPatch, path: str
    ) -> None:
        _pin_allowed_origins(monkeypatch)
        response = _client().post(
            path,
            headers={
                "Origin": CROSS_SITE_ORIGIN,
                CSRF_HEADER: CSRF_HEADER_VALUE,
                "Sec-Fetch-Site": "same-origin",  # attacker-supplied; Origin wins
            },
        )
        _assert_csrf_origin_403(response, REASON_ORIGIN_DENIED)

    @pytest.mark.parametrize("path", COOKIE_WRITE_PATHS)
    def test_post_missing_origin_disallowed_referer_is_403(
        self, monkeypatch: pytest.MonkeyPatch, path: str
    ) -> None:
        _pin_allowed_origins(monkeypatch)
        response = _client().post(
            path,
            headers={
                "Referer": f"{CROSS_SITE_ORIGIN}/forged",
                CSRF_HEADER: CSRF_HEADER_VALUE,
            },
        )
        _assert_csrf_origin_403(response, REASON_ORIGIN_DENIED)

    @pytest.mark.parametrize("path", COOKIE_WRITE_PATHS)
    def test_post_cross_site_fetch_metadata_is_403(
        self, monkeypatch: pytest.MonkeyPatch, path: str
    ) -> None:
        """Modern browsers: Sec-Fetch-Site: cross-site is rejected even if Origin looks allowed."""
        _pin_allowed_origins(monkeypatch)
        response = _client().post(
            path,
            headers={
                "Origin": ALLOWED_ORIGIN,
                CSRF_HEADER: CSRF_HEADER_VALUE,
                "Sec-Fetch-Site": "cross-site",
            },
        )
        _assert_csrf_origin_403(response, REASON_FETCH_METADATA)

    @pytest.mark.parametrize("path", COOKIE_WRITE_PATHS)
    def test_post_missing_csrf_header_is_403(
        self, monkeypatch: pytest.MonkeyPatch, path: str
    ) -> None:
        _pin_allowed_origins(monkeypatch)
        response = _client().post(
            path,
            headers={
                "Origin": ALLOWED_ORIGIN,
                "Sec-Fetch-Site": "same-origin",
            },
        )
        _assert_csrf_origin_403(response, REASON_CSRF_HEADER)

    @pytest.mark.parametrize("path", COOKIE_WRITE_PATHS)
    def test_post_wrong_csrf_header_is_403(
        self, monkeypatch: pytest.MonkeyPatch, path: str
    ) -> None:
        _pin_allowed_origins(monkeypatch)
        response = _client().post(
            path,
            headers={
                "Origin": ALLOWED_ORIGIN,
                CSRF_HEADER: "true",  # must be exactly "1"
                "Sec-Fetch-Site": "same-origin",
            },
        )
        _assert_csrf_origin_403(response, REASON_CSRF_HEADER)


class TestCsrfOriginAllow:
    """Positive CSRF/Origin gate: must not 403 for CSRF/Origin reasons."""

    @pytest.mark.parametrize("path", COOKIE_WRITE_PATHS)
    def test_post_allowed_origin_and_csrf_header_is_not_csrf_reject(
        self, monkeypatch: pytest.MonkeyPatch, path: str
    ) -> None:
        _pin_allowed_origins(monkeypatch)
        response = _client().post(
            path,
            headers={
                "Origin": ALLOWED_ORIGIN,
                CSRF_HEADER: CSRF_HEADER_VALUE,
                "Sec-Fetch-Site": "same-origin",
            },
        )
        _assert_not_csrf_origin_reject(response)

    @pytest.mark.parametrize("path", COOKIE_WRITE_PATHS)
    def test_post_origin_fallback_to_allowed_referer_is_not_csrf_reject(
        self, monkeypatch: pytest.MonkeyPatch, path: str
    ) -> None:
        """Origin missing: explicit Referer fallback to an allowlisted origin.

        Sec-Fetch-Site omitted = non-modern-browser Origin/Referer path
        (docs/architecture/02-admin-auth.md §9.1: 缺失时按明确策略校验 Referer).
        """
        _pin_allowed_origins(monkeypatch)
        response = _client().post(
            path,
            headers={
                "Referer": f"{ALLOWED_ORIGIN}/admin/login",
                CSRF_HEADER: CSRF_HEADER_VALUE,
            },
        )
        _assert_not_csrf_origin_reject(response)


class TestAuthCookieWritesAreNotGet:
    """GET must not succeed as a write on cookie-backed auth paths. 405 is OK."""

    @pytest.mark.parametrize("path", COOKIE_WRITE_PATHS)
    def test_get_is_not_a_successful_write(
        self, monkeypatch: pytest.MonkeyPatch, path: str
    ) -> None:
        _pin_allowed_origins(monkeypatch)
        response = _client().get(
            path,
            headers={
                "Origin": ALLOWED_ORIGIN,
                CSRF_HEADER: CSRF_HEADER_VALUE,
                "Sec-Fetch-Site": "same-origin",
            },
        )
        assert not (200 <= response.status_code < 300), (
            f"GET {path} must not succeed as a write (got HTTP {response.status_code})"
        )
