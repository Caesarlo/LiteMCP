"""LiteMCP backend HTTP entrypoint: liveness and readiness endpoints.

``GET /livez`` (M0-BE-001) reports process liveness only and never reaches
out to external dependencies (docs/architecture/07-observability.md L26).

``GET /readyz`` (M0-BE-002) returns a *real* readiness result derived from
the current state of the database and Redis dependencies. Readiness is
decided by two synchronous module-level probes — ``probe_database`` and
``probe_redis`` — that are looked up on this module at request time so tests
can monkeypatch them (docs/architecture/07-observability.md L418-L420). Each
probe returns a bool: ``True`` = healthy, ``False`` = unhealthy.

The probes wrap async drivers in a synchronous bool contract. The /readyz
handler runs them on the thread pool executor (``asyncio.to_thread``) so the
event loop is never blocked and ``asyncio.run`` never collides with a running
loop. The probes additionally fall back to a dedicated daemon thread when
invoked directly from inside a running event loop.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable, Coroutine
from typing import Any

import redis
import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from litemcp.core.config import get_settings
from litemcp.correlation import CorrelationIdMiddleware
from litemcp.security.redaction import (
    SecretRedactionMiddleware,
    SecretRedactor,
    install_logging_redaction,
)

# Short probe timeout so a stalled dependency fails fast instead of hanging
# /readyz.
_PROBE_TIMEOUT = 2.0

app = FastAPI(title="LiteMCP")
_app_redactor = SecretRedactor.from_environment()
install_logging_redaction(_app_redactor)
app.add_middleware(CorrelationIdMiddleware)
app.add_middleware(SecretRedactionMiddleware, redactor=_app_redactor)


@app.get("/livez")
async def livez() -> dict[str, str]:
    """Report process liveness only; never reach out to dependencies."""
    return {"status": "ok"}


def _run_in_new_loop(coro: Coroutine[Any, Any, bool]) -> bool:
    """Run ``coro`` to completion on a fresh event loop.

    Wraps ``asyncio.run``; the type ignore papers over typeshed's overly
    strict ``Never`` return-typing for ``asyncio.run``.
    """
    return asyncio.run(coro)  # type: ignore[arg-type]


def _run_async_probe(probe: Callable[[], Coroutine[Any, Any, bool]]) -> bool:
    """Execute an async readiness probe and return its bool result.

    In the normal path the /readyz handler already runs probes on a thread
    pool worker (via ``asyncio.to_thread``), where ``asyncio.run`` is safe.
    If a probe is invoked directly from inside a running event loop, the
    async work is executed on a dedicated daemon thread instead, so
    ``asyncio.run`` never collides with an already-running loop and the probe
    never crashes nor hangs past the join timeout.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return _run_in_new_loop(probe())

    outcome: list[bool] = []

    def _target() -> None:
        try:
            outcome.append(_run_in_new_loop(probe()))
        except Exception:  # noqa: BLE001 - probe must never raise upward
            outcome.append(False)

    thread = threading.Thread(target=_target, daemon=True)
    thread.start()
    thread.join(timeout=_PROBE_TIMEOUT + 1.0)
    return bool(outcome) and outcome[0]


async def _probe_database_async() -> bool:
    """Async implementation: connect and run ``SELECT 1`` against the DB."""
    settings = get_settings()
    url = settings.database_url.get_secret_value()
    connect_args: dict[str, Any] = {}
    if "postgres" in url.split("://", 1)[0].lower():
        # asyncpg accepts a per-connect timeout in seconds.
        connect_args = {"timeout": _PROBE_TIMEOUT}
    engine: AsyncEngine = create_async_engine(
        url,
        pool_pre_ping=True,
        connect_args=connect_args,
    )
    try:
        async with asyncio.timeout(_PROBE_TIMEOUT):
            async with engine.connect() as conn:
                await conn.execute(sa.text("SELECT 1"))
        return True
    except Exception:  # noqa: BLE001 - probe must report False, not raise
        return False
    finally:
        await engine.dispose()


def probe_database() -> bool:
    """Synchronous database readiness probe (True = healthy)."""
    try:
        return _run_async_probe(_probe_database_async)
    except Exception:  # noqa: BLE001 - probe must report False, not raise
        return False


def probe_redis() -> bool:
    """Synchronous Redis readiness probe (True = healthy)."""
    try:
        settings = get_settings()
        url = settings.redis_url.get_secret_value()
        client = redis.Redis.from_url(
            url,
            socket_connect_timeout=_PROBE_TIMEOUT,
            socket_timeout=_PROBE_TIMEOUT,
            decode_responses=True,
        )
        try:
            return bool(client.ping())
        finally:
            client.close()
    except Exception:  # noqa: BLE001 - probe must report False, not raise
        return False


@app.get("/readyz")
async def readyz() -> JSONResponse:
    """Derive real readiness from the current database/Redis probe state.

    Probes are resolved on this module at request time so tests can
    monkeypatch them. They run on the thread pool executor so the event loop
    stays responsive and the sync ``asyncio.run``-based probes execute on a
    thread without a running loop.
    """
    db_ok = await asyncio.to_thread(probe_database)
    redis_ok = await asyncio.to_thread(probe_redis)

    components = [
        {
            "name": "database",
            "status": "ready" if db_ok else "not_ready",
            "reason": "ok" if db_ok else "connection_failed",
        },
        {
            "name": "redis",
            "status": "ready" if redis_ok else "not_ready",
            "reason": "ok" if redis_ok else "connection_failed",
        },
    ]
    ready = db_ok and redis_ok
    return JSONResponse(
        status_code=200 if ready else 503,
        content={"status": "ready" if ready else "not_ready", "components": components},
    )
