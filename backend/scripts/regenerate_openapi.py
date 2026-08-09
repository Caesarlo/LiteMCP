"""Regenerate the committed OpenAPI snapshot from the live FastAPI app.

M0-CONTRACT-001 requires the backend OpenAPI document to be generated from
the FastAPI app (docs/architecture/09-verification.md 4.2, L139), with any
destructive change to the API contract explicitly approved and reflected in
the committed snapshot before it lands. This script is the reproducible
approval path: run it (``make update-openapi-snapshot``) after an intended
contract change, inspect the diff, and commit the regenerated snapshot.

Serialization is deterministic (``sort_keys=True``, fixed indent, trailing
newline) so the file is byte-stable: re-running against an unchanged app
produces an identical file and never trips the drift gate.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Make ``litemcp`` importable when run as ``python scripts/regenerate_openapi.py``
# from the backend directory (pytest already gets this via pyproject pythonpath).
_BACKEND_DIR = Path(__file__).resolve().parents[1]
_SRC_DIR = _BACKEND_DIR / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from litemcp.main import app  # noqa: E402  (import order required by sys.path setup)

SNAPSHOT_PATH = _SRC_DIR / "litemcp" / "openapi.json"


def serialize_snapshot(spec: dict) -> str:
    """Deterministic, byte-stable serialization shared by the snapshot gate."""
    return json.dumps(spec, indent=2, sort_keys=True) + "\n"


def main() -> None:
    snapshot = serialize_snapshot(app.openapi())
    SNAPSHOT_PATH.write_text(snapshot, encoding="utf-8")
    print(f"Wrote OpenAPI snapshot to {SNAPSHOT_PATH.resolve()}")


if __name__ == "__main__":
    main()
