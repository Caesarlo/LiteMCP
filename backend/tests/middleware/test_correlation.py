"""Contract tests for the LiteMCP correlation-id middleware (M0-BE-003).

Feature behavior: every management and gateway request is assigned a
propagatable ``request_id``/``correlation_id``.

This file pins the observable contract declared by
docs/architecture/07-observability.md (L75, L80, L212-213, L227) and
docs/architecture/05-agent-gateway.md (L408). The three aspects the feature
verification names -- request header, response header, and context
propagation -- are each pinned below.

These tests MUST currently FAIL: the application has no correlation
middleware yet, so the assertions below point directly at the missing
behavior (absent ``X-Request-Id`` response header, or an exception when
handling code tries to read ``request.state.request_id``).

CONTRACT PINNED (the implementer makes these tests pass):

1. Ingress validation (docs/07 L75):
   - An inbound ``X-Request-ID`` is accepted only if it matches
     ``[A-Za-z0-9._-]+`` and is between 1 and 128 bytes long.
   - An absent or invalid inbound ``X-Request-ID`` must never be forwarded:
     the middleware regenerates a fresh, valid request_id instead.
   - HTTP inbound always gets its own independent request_id (docs/07 L80).

2. Response echo (docs/05 L408, docs/07 L75):
   - Every response -- success, not-found, whichever endpoint -- carries an
     ``X-Request-Id`` response header.
   - Its value equals the request_id that was used to process that request
     (the valid inbound value, or the regenerated value).

3. Context propagation (docs/07 L212-213, L227):
   - Request handling code can read the current request's request_id via
     ``request.state.request_id`` (Starlette per-request state, populated by
     the middleware).
   - The value the handler reads MUST equal the request_id echoed in the
     response header, so the whole request correlates on one value.

TEST-ONLY PROBE: ``_PROBE_PATH`` is a route registered on the imported app
for the sole purpose of observing what request handling code can read. It is
test scaffolding and must never appear in the real application.
"""

from __future__ import annotations

import re

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from litemcp.main import app

# ---------------------------------------------------------------------------
# Test-only probe route (scaffolding, never part of the real application)
# ---------------------------------------------------------------------------

PROBE_PATH = "/__test__/probe/request-id"

_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")
_MAX_REQUEST_ID_BYTES = 128


@pytest.fixture(scope="module", autouse=True)
def _probe_route():
    """Register the test-only probe route for this module, then remove it.

    Pins docs/07 L212-213/L227: the middleware must expose the current
    request_id to request handling code via ``request.state.request_id``.

    The route is registered lazily (not at module import time) and removed
    after this module's tests so the shared ``app`` is restored to its
    pristine state. It must never leak into the committed OpenAPI snapshot
    gate (M0-CONTRACT-001) or into other test modules that observe the app.
    """

    @app.get(PROBE_PATH, name="test_probe_request_id")
    def _probe_request_id(request: Request) -> dict:
        return {"request_id": request.state.request_id}

    yield

    app.routes[:] = [
        route
        for route in app.routes
        if getattr(route, "name", None) != "test_probe_request_id"
    ]
    app.openapi_schema = None  # drop any cached schema built with the probe route


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _assert_valid_request_id(value: str | None) -> None:
    """Pins docs/07 L75: request_id is ``[A-Za-z0-9._-]`` and 1-128 bytes."""
    assert isinstance(value, str), f"expected a string request_id, got {value!r}"
    assert 1 <= len(value) <= _MAX_REQUEST_ID_BYTES, (
        f"request_id length {len(value)} must be within 1..{_MAX_REQUEST_ID_BYTES}"
    )
    assert _REQUEST_ID_RE.fullmatch(value) is not None, (
        f"request_id {value!r} contains characters outside [A-Za-z0-9._-]"
    )


def _client() -> TestClient:
    return TestClient(app)


# ---------------------------------------------------------------------------
# 1. Request header: ingress validation of X-Request-ID (docs/07 L75, L80)
# ---------------------------------------------------------------------------


class TestInboundRequestIdValidation:
    """Ingress validates X-Request-ID and regenerates a valid ID on failure."""

    def test_valid_inbound_id_is_accepted_and_echoed(self) -> None:
        inbound = "req_01J3Z7KQ-test.id-2026"
        response = _client().get("/livez", headers={"X-Request-ID": inbound})

        assert response.status_code == 200
        echoed = response.headers.get("x-request-id")
        assert echoed == inbound, (
            f"valid inbound X-Request-ID {inbound!r} must be preserved and "
            f"echoed, got {echoed!r}"
        )

    def test_max_length_128_inbound_id_is_accepted(self) -> None:
        inbound = "a" * 128
        response = _client().get("/livez", headers={"X-Request-ID": inbound})

        assert response.headers.get("x-request-id") == inbound

    @pytest.mark.parametrize(
        "bad_inbound",
        [
            "has space",       # space is not in [A-Za-z0-9._-]
            "bad/id",          # '/' is not allowed
            "bad$id",          # '$' is not allowed
            "bad!id",          # '!' is not allowed
            "a" * 129,         # 129 bytes > 128
            "",                # empty violates the 1..128 range
        ],
    )
    def test_invalid_inbound_id_is_regenerated(self, bad_inbound: str) -> None:
        response = _client().get("/livez", headers={"X-Request-ID": bad_inbound})

        echoed = response.headers.get("x-request-id")
        assert echoed is not None, "response must carry X-Request-Id"
        assert echoed != bad_inbound, (
            f"invalid inbound X-Request-ID {bad_inbound!r} must be regenerated, "
            f"not echoed"
        )
        _assert_valid_request_id(echoed)

    def test_missing_inbound_id_still_yields_a_request_id(self) -> None:
        response = _client().get("/livez")

        echoed = response.headers.get("x-request-id")
        assert echoed is not None, (
            "docs/07 L75/L80: absent X-Request-ID must be generated by the "
            "server, never left missing"
        )
        _assert_valid_request_id(echoed)


# ---------------------------------------------------------------------------
# 2. Response header: uniform X-Request-Id echo (docs/05 L408, docs/07 L75)
# ---------------------------------------------------------------------------


class TestResponseHeaderContract:
    """Every response carries X-Request-Id matching the request_id used."""

    def test_probe_response_carries_x_request_id_without_inbound_id(self) -> None:
        response = _client().get(PROBE_PATH)

        assert response.status_code == 200
        assert response.headers.get("x-request-id") is not None

    def test_existing_endpoint_response_carries_x_request_id(self) -> None:
        response = _client().get("/livez")

        assert response.status_code == 200
        assert response.headers.get("x-request-id") is not None

    def test_not_found_response_carries_x_request_id(self) -> None:
        response = _client().get("/__no_such_route_here__")

        assert response.status_code == 404
        assert response.headers.get("x-request-id") is not None, (
            "docs/05 L408: responses uniformly include X-Request-Id, "
            "including error responses"
        )

    def test_echoed_header_matches_used_request_id(self) -> None:
        response = _client().get(PROBE_PATH)

        probe_id = response.json()["request_id"]
        echoed = response.headers.get("x-request-id")
        assert echoed is not None
        assert echoed == probe_id, (
            "the X-Request-Id response header must equal the request_id the "
            "handler saw, so the whole request correlates on one value"
        )


# ---------------------------------------------------------------------------
# 3. Context propagation: handling code can read the current request_id
# ---------------------------------------------------------------------------


class TestRequestIdContextPropagation:
    """Handling code reads the current request_id via request.state."""

    def test_handler_reads_valid_inbound_request_id(self) -> None:
        inbound = "req_propagation.2026-08-09"
        response = _client().get(PROBE_PATH, headers={"X-Request-ID": inbound})

        assert response.status_code == 200
        seen_in_handler = response.json()["request_id"]
        assert seen_in_handler == inbound, (
            f"handler must read the valid inbound request_id, got "
            f"{seen_in_handler!r}"
        )
        assert response.headers.get("x-request-id") == inbound

    def test_handler_reads_the_regenerated_request_id(self) -> None:
        bad_inbound = "invalid request id!"
        response = _client().get(PROBE_PATH, headers={"X-Request-ID": bad_inbound})

        seen_in_handler = response.json()["request_id"]
        assert seen_in_handler != bad_inbound
        _assert_valid_request_id(seen_in_handler)
        assert response.headers.get("x-request-id") == seen_in_handler, (
            "the request_id the handler reads must be the same one echoed in "
            "the response header"
        )

    def test_each_request_gets_an_independent_request_id(self) -> None:
        client = _client()
        first = client.get(PROBE_PATH).headers.get("x-request-id")
        second = client.get(PROBE_PATH).headers.get("x-request-id")

        assert first is not None and second is not None
        assert first != second, (
            "docs/07 L80: HTTP inbound always generates an independent "
            "request_id per request"
        )
