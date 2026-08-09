"""Contract tests for the backend health endpoints.

``GET /livez`` (M0-BE-001) returns HTTP 200 and a stable JSON response
structure while the process is alive. Per docs/architecture/07-observability.md,
``/livez`` only reflects whether the process needs a restart — it must not
reach out to external dependencies and fails only when the event loop is stuck
or the process is unrecoverable (docs/architecture/08-implementation-plan.md
L100: live reflects process liveness, independent of dependency readiness).

``GET /readyz`` (M0-BE-002) returns a *real* readiness result derived from the
current state of the database and Redis dependencies; see TestReadyzContract
for the failure-injection seam and response contract it pins.
"""

from __future__ import annotations

import re

from fastapi.testclient import TestClient

import litemcp.main as main_module
from litemcp.main import app


class TestLivezContract:
    """``GET /livez`` reports process liveness with a stable response."""

    def test_livez_returns_200_when_alive(self) -> None:
        client = TestClient(app)

        response = client.get("/livez")

        assert response.status_code == 200

    def test_livez_returns_json_content_type(self) -> None:
        client = TestClient(app)

        response = client.get("/livez")

        assert response.headers["content-type"].startswith("application/json")

    def test_livez_returns_stable_status_payload(self) -> None:
        client = TestClient(app)

        response = client.get("/livez")

        payload = response.json()
        assert isinstance(payload, dict)
        assert payload.get("status") == "ok"


# ---------------------------------------------------------------------------
# M0-BE-002 — /readyz readiness contract
# ---------------------------------------------------------------------------
#
# GET /readyz MUST return a *real* readiness result derived from the current
# state of the database and Redis dependencies (docs/architecture/07-
# observability.md L26, L418-L420). Unlike /livez, it probes dependencies.
#
# FAILURE-INJECTION SEAM (the implementer MUST provide this — the tests below
# depend on it, and the test environment has no real database or Redis
# service):
#
#     The litemcp.main module MUST expose two synchronous probe functions that
#     return bool (True = healthy, False = unhealthy), looked up on the module
#     at request time so that monkeypatching them is observable:
#
#         probe_database() -> bool
#         probe_redis()    -> bool
#
#     GET /readyz MUST call these probes and derive readiness from their
#     results. The tests monkeypatch them to simulate "all dependencies
#     healthy" and "one dependency down" without contacting any external
#     service.
#
# RESPONSE CONTRACT:
#
#     * every probe healthy   -> HTTP 200,  {"status": "ready", ...}
#     * at least one probe unhealthy -> HTTP 5xx, {"status": "not_ready", ...}
#     * body shape: {"status": str, "components": [{"name": str,
#       "status": str, "reason": str}, ...]} — each component's status MUST
#       mirror the corresponding probe result.
#     * the body carries only component/status/reason-level detail; it must
#       never include DSNs, hosts, connection strings, keys/secrets, or stack
#       traces (docs/architecture/07-observability.md L420).


def _component_named(components: list[dict], name_pattern: str) -> dict:
    """Return the single component whose ``name`` matches ``name_pattern``."""
    for entry in components:
        if re.search(name_pattern, str(entry.get("name", "")), re.IGNORECASE):
            return entry
    raise AssertionError(
        f"readiness response missing a component matching {name_pattern!r}: "
        f"{components!r}"
    )


_FORBIDDEN_READY_DETAILS = [
    r"://",                                  # any host-bearing URI / DSN
    r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}",  # raw IP address
    r"localhost",
    r"host=",
    r"dbname=",
    r"port=",
    r"password",
    r"passwd",
    r"secret",
    r"api[_-]?key",
    r"bearer\s",
    r"token",
    r"private[_-]?key",
    r"begin [a-z ]*private key",
    r"traceback",
    r'file "',
    r"line \d+",
    r"\.py\b",
    r"at 0x[0-9a-f]+",
]


def _assert_no_sensitive_or_operational_leak(text: str) -> None:
    """Pin docs/07 L420: /readyz bodies expose only component/status/reason."""
    lowered = text.lower()
    for pattern in _FORBIDDEN_READY_DETAILS:
        assert not re.search(pattern, lowered), (
            "/readyz response leaked forbidden operational detail "
            f"(pattern {pattern!r}): {text!r}"
        )


class TestReadyzContract:
    """``GET /readyz`` reports real readiness from the DB/Redis probe state."""

    def test_readyz_all_dependencies_healthy_returns_200(self, monkeypatch) -> None:
        monkeypatch.setattr(main_module, "probe_database", lambda: True)
        monkeypatch.setattr(main_module, "probe_redis", lambda: True)

        client = TestClient(app)
        response = client.get("/readyz")

        assert response.status_code == 200, (
            "readyz must be HTTP 200 when every dependency is healthy"
        )
        payload = response.json()
        assert payload.get("status") == "ready", (
            f"expected 'ready' when dependencies are healthy, got {payload!r}"
        )
        _assert_no_sensitive_or_operational_leak(response.text)

        components = payload.get("components")
        assert isinstance(components, list) and len(components) >= 2, (
            "readyz must report at least database and redis components"
        )
        for entry in components:
            assert set(entry) == {"name", "status", "reason"}, (
                "each readiness component must carry exactly name/status/reason"
            )
            assert entry["reason"], "each component must carry a reason code"
        assert _component_named(components, r"postgres|database|db")["status"] == "ready"
        assert _component_named(components, r"redis")["status"] == "ready"

    def test_readyz_database_down_returns_5xx(self, monkeypatch) -> None:
        monkeypatch.setattr(main_module, "probe_database", lambda: False)
        monkeypatch.setattr(main_module, "probe_redis", lambda: True)

        client = TestClient(app)
        response = client.get("/readyz")

        assert response.status_code >= 500, (
            "readyz must return 5xx when the database dependency is down"
        )
        payload = response.json()
        assert payload.get("status") == "not_ready", (
            f"expected 'not_ready' when a dependency is down, got {payload!r}"
        )
        _assert_no_sensitive_or_operational_leak(response.text)

        components = payload.get("components")
        assert isinstance(components, list) and len(components) >= 2, (
            "readyz must report at least database and redis components"
        )
        db = _component_named(components, r"postgres|database|db")
        assert db["status"] != "ready", "failed database component must not report ready"
        assert db["reason"], "failed component must carry a reason code"
        redis = _component_named(components, r"redis")
        assert redis["status"] == "ready", (
            "healthy redis component must still report ready"
        )

    def test_readyz_redis_down_returns_5xx(self, monkeypatch) -> None:
        monkeypatch.setattr(main_module, "probe_database", lambda: True)
        monkeypatch.setattr(main_module, "probe_redis", lambda: False)

        client = TestClient(app)
        response = client.get("/readyz")

        assert response.status_code >= 500, (
            "readyz must return 5xx when the Redis dependency is down"
        )
        payload = response.json()
        assert payload.get("status") == "not_ready", (
            f"expected 'not_ready' when a dependency is down, got {payload!r}"
        )
        _assert_no_sensitive_or_operational_leak(response.text)

        components = payload.get("components")
        assert isinstance(components, list) and len(components) >= 2, (
            "readyz must report at least database and redis components"
        )
        redis = _component_named(components, r"redis")
        assert redis["status"] != "ready", "failed redis component must not report ready"
        assert redis["reason"], "failed component must carry a reason code"
        db = _component_named(components, r"postgres|database|db")
        assert db["status"] == "ready", (
            "healthy database component must still report ready"
        )
