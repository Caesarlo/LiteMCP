"""Contract tests for the OpenAPI snapshot gate (M0-CONTRACT-001).

The backend is a FastAPI app. docs/architecture/09-verification.md §4.2 (L139)
requires the OpenAPI document to be generated from the FastAPI app and any
destructive diff to it to be explicitly approved and reflected in a committed
snapshot before it lands; L25/L131 make an unapproved OpenAPI generation diff a
per-commit blocker. docs/architecture/08-implementation-plan.md L334 puts the
OpenAPI snapshot / breaking-diff check in the PR quick gate, and
docs/architecture/06-frontend.md L449 will have the frontend generate types
from the backend OpenAPI — so a stable, committed snapshot must exist.

These tests pin that gate:

* a committed snapshot must exist at backend/src/litemcp/openapi.json;
* the snapshot must be a structurally valid OpenAPI 3.1.0 document exposing
  both /livez and /readyz;
* the spec regenerated NOW from the live app (app.openapi()) must deep-equal
  the committed snapshot — any change to the API contract that is not
  reflected in the committed snapshot makes this comparison fail;
* the very same comparison must detect a deliberately stale/mutated spec
  (negative case proving the gate catches drift).

Assertions are kept robust to the exact current spec content: they check
structural properties (openapi version, presence of the two health paths) plus
the deep-equality gate, never brittle snapshots of operationIds or
descriptions.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from litemcp.main import app

# Committed snapshot produced from the FastAPI app (backend/src/litemcp).
SNAPSHOT_PATH = (
    Path(__file__).resolve().parents[2] / "src" / "litemcp" / "openapi.json"
)

# The contract surface that must always be present in the document.
EXPECTED_PATHS = {"/livez", "/readyz"}


def _load_snapshot() -> dict:
    """Return the committed OpenAPI snapshot parsed as JSON."""
    with SNAPSHOT_PATH.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _assert_spec_matches_snapshot(live_spec: dict, snapshot_spec: dict) -> None:
    """The drift gate: assert a spec deep-equals the committed snapshot.

    Comparison is on parsed JSON structures (dict equality), never raw bytes,
    so key order or formatting never matters. Any difference between the two
    is an unapproved contract change and fails the gate.
    """
    if live_spec == snapshot_spec:
        return
    sections = sorted(
        key
        for key in set(live_spec) | set(snapshot_spec)
        if live_spec.get(key) != snapshot_spec.get(key)
    )
    raise AssertionError(
        "OpenAPI drift detected: the live spec no longer matches the committed "
        f"snapshot ({SNAPSHOT_PATH.name}). Diverging top-level sections: "
        f"{sections}. If the change is intended, regenerate and commit the "
        "snapshot; otherwise this is an unapproved breaking change."
    )


def _assert_live_spec_matches_snapshot() -> None:
    """Regenerate the spec from the live app NOW and run the drift gate."""
    _assert_spec_matches_snapshot(app.openapi(), _load_snapshot())


class TestOpenApiSnapshotGate:
    """The committed OpenAPI snapshot exists, is valid, and matches the app."""

    def test_snapshot_file_exists(self) -> None:
        assert SNAPSHOT_PATH.is_file(), (
            f"committed OpenAPI snapshot missing at {SNAPSHOT_PATH}; the "
            "snapshot must be generated from the FastAPI app and committed"
        )

    def test_snapshot_is_valid_openapi_document(self) -> None:
        snapshot = _load_snapshot()
        assert snapshot.get("openapi") == "3.1.0", (
            f"snapshot must declare OpenAPI 3.1.0, got {snapshot.get('openapi')!r}"
        )
        info = snapshot.get("info")
        assert isinstance(info, dict) and info, (
            "snapshot must carry a non-empty info object"
        )
        paths = snapshot.get("paths")
        assert isinstance(paths, dict), (
            "snapshot paths must be an object mapping paths to operations"
        )
        assert EXPECTED_PATHS.issubset(paths), (
            f"snapshot must document both /livez and /readyz; "
            f"got {sorted(paths)}"
        )

    def test_live_spec_matches_committed_snapshot(self) -> None:
        """The drift gate: the spec regenerated now equals the committed snapshot."""
        _assert_live_spec_matches_snapshot()

    def test_drift_gate_detects_unapproved_change(self, monkeypatch) -> None:
        """Negative case: the same comparison catches a stale/mutated spec.

        A spec that diverges from the committed snapshot (here an extra,
        unapproved path) must make the gate report the drift. This exercises
        the real comparison code path with a patched live spec, not a
        duplicated stub.
        """
        snapshot = _load_snapshot()
        drifted = copy.deepcopy(snapshot)
        drifted["paths"] = dict(drifted.get("paths", {}))
        drifted["paths"]["/_drift_probe"] = {
            "get": {"responses": {"200": {"description": "unapproved path"}}}
        }

        monkeypatch.setattr(app, "openapi", lambda: drifted)

        with pytest.raises(AssertionError, match="OpenAPI drift detected"):
            _assert_live_spec_matches_snapshot()
