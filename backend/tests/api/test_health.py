"""Tests for the backend liveness contract (M0-BE-001).

Covers the feature's declared behavior: ``GET /livez`` returns HTTP 200 and a
stable JSON response structure while the process is alive. Per
docs/architecture/07-observability.md, ``/livez`` only reflects whether the
process needs a restart — it must not reach out to external dependencies and
fails only when the event loop is stuck or the process is unrecoverable
(docs/architecture/08-implementation-plan.md L100: live reflects process
liveness, independent of dependency readiness).
"""

from __future__ import annotations

from fastapi.testclient import TestClient

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
