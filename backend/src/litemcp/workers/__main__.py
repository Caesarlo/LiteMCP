"""Run the LiteMCP worker process.

M0-BOOT-001 placeholder entrypoint. The real long-running worker jobs
(build, sync, GC, key rotation) land in M3; for now this keeps the compose
``worker`` service alive and logs that no real jobs are scheduled yet.
"""

from __future__ import annotations

import asyncio
import logging
import os

LOGGER = logging.getLogger("litemcp.workers")


async def _placeholder_loop() -> None:
    """Idle placeholder worker loop for M0-BOOT-001."""
    worker_id = os.environ.get("LITEMCP_WORKER_ID", "local")
    LOGGER.info(
        "litemcp worker started (worker_id=%s) [M0-BOOT-001 placeholder]; "
        "real worker jobs (build, sync, GC, key rotation) land in M3",
        worker_id,
    )
    while True:
        await asyncio.sleep(3600)


def main() -> None:
    logging.basicConfig(level=os.environ.get("LITEMCP_LOG_LEVEL", "INFO"))
    asyncio.run(_placeholder_loop())


if __name__ == "__main__":
    main()
