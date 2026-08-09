"""Worker entry points for LiteMCP background jobs.

M0-BOOT-001 placeholder: the real long-running worker jobs (build, sync,
GC, key rotation) land in M3 — see docs/architecture/00-overview.md §5.3
and docs/architecture/08-implementation-plan.md §M3. This package exists
so the compose ``worker`` service can start via ``python -m litemcp.workers``
and stay alive without implementing real job logic yet.
"""

from __future__ import annotations

__all__: list[str] = []
