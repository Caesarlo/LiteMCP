"""DB-layer login lockout contract (M2-AUTH-005).

docs/architecture/02-admin-auth.md §6.1 / §6.3 / §18 (lockout bits only):
after a configurable number of failures inside an observation window, the
user row is written ``status=locked`` with ``locked_until``. A successful
login zeros the failure count and window. ``disabled`` outranks lock
expiry and must never be auto-reactivated. An expired lock is cleared
back to ``active`` (count and window reset) *before* the current password
attempt is processed. Concurrent failed logins must not drop counts.

This file is scoped to the DB-row state machine. There is no HTTP login
endpoint, Redis source-IP limiter, JWT/session issuance, admin unlock, or
audit persistence here. Password hashing is also out of scope: the caller
is assumed to have already decided success vs failure (or to consult the
pre-verify gate and skip verification when it is denied).

Public API contract this test pins (RED until M2-AUTH-005 exists), all
exposed from ``litemcp.auth.lockout``. Every function runs inside the
caller's already-open transaction and must not ``begin()``/``commit()``
itself. Each function loads the target row by id with a row lock
(``SELECT ... FOR UPDATE``), so two concurrent callers cannot lose
increments.

    async def prepare_login_attempt(
        session: AsyncSession, user_id: uuid.UUID
    ) -> LoginAttemptDecision

Row-lock the user, process an expired lock, then decide whether the
caller may verify a password. ``LoginAttemptDecision`` has:

- ``may_verify_password: bool`` -- True only when the user is ``active``
  after expired-lock handling.
- ``denial_reason: str | None`` -- ``"disabled"`` or ``"locked"`` when
  verification is forbidden; ``None`` otherwise.
- ``user: User`` -- the locked row after any expired-lock mutation.

    async def record_login_failure(
        session: AsyncSession, user_id: uuid.UUID
    ) -> User

Record one failed attempt on an active user: first failure in a window
creates ``failed_login_window_started_at``; a failure after the window
has elapsed restarts the window at count 1; reaching
``ADMIN_LOGIN_FAILURE_THRESHOLD`` writes ``status=locked`` and
``locked_until = now + ADMIN_LOCK_SECONDS``.

    async def record_login_success(
        session: AsyncSession, user_id: uuid.UUID
    ) -> User

Zero ``failed_login_count`` and ``failed_login_window_started_at``, and
leave the user unlocked (``status=active``, ``locked_until`` cleared).

Settings (``LITEMCP_*`` / ``litemcp.core.config.Settings``), defaults
from architecture §18:

- ``ADMIN_LOGIN_FAILURE_THRESHOLD`` -> ``admin_login_failure_threshold = 5``
- ``ADMIN_LOGIN_FAILURE_WINDOW_SECONDS`` -> ``admin_login_failure_window_seconds = 900``
- ``ADMIN_LOCK_SECONDS`` -> ``admin_lock_seconds = 900``

These tests run against real PostgreSQL and MySQL databases, provisioned
fresh per test and migrated to head. A missing ``litemcp.auth.lockout``
module is the expected RED failure -- the lockout flow does not exist yet.
"""

from __future__ import annotations

import asyncio
import os
import threading
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from litemcp.db.models import User

BACKEND_DIR = Path(__file__).resolve().parents[2]
ALEMBIC_INI = BACKEND_DIR / "alembic.ini"
MIGRATIONS_DIR = BACKEND_DIR / "migrations"
POSTGRES_URL = os.environ.get(
    "LITEMCP_TEST_POSTGRES_URL",
    "postgresql+asyncpg://litemcp:litemcp@localhost:5433/litemcp",
)
MYSQL_URL = os.environ.get(
    "LITEMCP_TEST_MYSQL_URL",
    "mysql+aiomysql://litemcp:litemcp@localhost:3307/litemcp",
)
MYSQL_ROOT_URL = os.environ.get(
    "LITEMCP_TEST_MYSQL_ROOT_URL",
    "mysql+aiomysql://root:litemcp-root@localhost:3307/mysql",
)

DEFAULT_THRESHOLD = 5
DEFAULT_WINDOW_SECONDS = 900
DEFAULT_LOCK_SECONDS = 900
LOCK_UNTIL_TOLERANCE_SECONDS = 15


def _make_config() -> Config:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    return cfg


def _upgrade(url: str) -> None:
    cfg = _make_config()
    cfg.set_main_option("sqlalchemy.url", url)
    errors: list[BaseException] = []

    def run() -> None:
        try:
            command.upgrade(cfg, "head")
        except Exception as exc:  # noqa: BLE001 - surfaced below
            errors.append(exc)

    thread = threading.Thread(target=run)
    thread.start()
    thread.join()
    if errors:
        raise errors[0]


def _db_name(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


async def _create_db(dialect: str, name: str) -> None:
    base = POSTGRES_URL if dialect == "postgres" else MYSQL_ROOT_URL
    engine = create_async_engine(base, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            if dialect == "postgres":
                await conn.exec_driver_sql(f'CREATE DATABASE "{name}"')
            else:
                await conn.exec_driver_sql(f"CREATE DATABASE `{name}`")
                await conn.exec_driver_sql(
                    "CREATE USER IF NOT EXISTS 'litemcp'@'%%' IDENTIFIED BY 'litemcp'"
                )
                await conn.exec_driver_sql(
                    f"GRANT ALL PRIVILEGES ON `{name}`.* TO 'litemcp'@'%%'"
                )
                await conn.exec_driver_sql("FLUSH PRIVILEGES")
    finally:
        await engine.dispose()


async def _drop_db(dialect: str, name: str) -> None:
    base = POSTGRES_URL if dialect == "postgres" else MYSQL_ROOT_URL
    engine = create_async_engine(base, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            if dialect == "postgres":
                await conn.exec_driver_sql(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
            else:
                await conn.exec_driver_sql(f"DROP DATABASE IF EXISTS `{name}`")
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def live_db(request: pytest.FixtureRequest) -> tuple[AsyncEngine, str]:
    dialect = request.param
    base_url = POSTGRES_URL if dialect == "postgres" else MYSQL_URL
    name = _db_name("litemcp_lockout")
    await _create_db(dialect, name)
    url = make_url(base_url).set(database=name).render_as_string(hide_password=False)
    try:
        _upgrade(url)
        engine = create_async_engine(url, pool_size=4, max_overflow=4)
    except Exception:
        await _drop_db(dialect, name)
        raise
    try:
        yield engine, dialect
    finally:
        await engine.dispose()
        await _drop_db(dialect, name)


@pytest.fixture
def lockout_settings(monkeypatch: pytest.MonkeyPatch):
    """Required Settings env so lockout code can call ``get_settings()``."""
    monkeypatch.setenv("LITEMCP_ENVIRONMENT", "dev")
    monkeypatch.setenv(
        "LITEMCP_DATABASE_URL",
        "postgresql+asyncpg://user:pass@localhost:5432/litemcp",
    )
    monkeypatch.setenv("LITEMCP_REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("LITEMCP_ENCRYPTION_KEYS", Fernet.generate_key().decode())

    from litemcp.core.config import get_settings

    get_settings.cache_clear()
    yield get_settings()
    get_settings.cache_clear()


@pytest.fixture
def lockout_api(lockout_settings):
    """Load the lockout contract (RED until M2-AUTH-005 exists)."""
    try:
        from litemcp.auth.lockout import (
            LoginAttemptDecision,
            prepare_login_attempt,
            record_login_failure,
            record_login_success,
        )
    except ImportError as exc:  # pragma: no cover - expected RED result
        raise AssertionError(
            "M2-AUTH-005 must expose litemcp.auth.lockout.prepare_login_attempt, "
            "litemcp.auth.lockout.record_login_failure, "
            "litemcp.auth.lockout.record_login_success, and "
            "litemcp.auth.lockout.LoginAttemptDecision"
        ) from exc
    return (
        prepare_login_attempt,
        record_login_failure,
        record_login_success,
        LoginAttemptDecision,
    )


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _placeholder_hash() -> str:
    return "$argon2id$v=19$m=65536,t=3,p=4$" + "a" * 22 + "$" + "b" * 43


async def _seed_user(
    engine: AsyncEngine,
    *,
    username: str,
    status: str = "active",
    failed_login_count: int = 0,
    failed_login_window_started_at: datetime | None = None,
    locked_until: datetime | None = None,
) -> User:
    now = datetime.now(UTC)
    user = User(
        id=uuid.uuid4(),
        username=username,
        username_normalized=username,
        password_hash=_placeholder_hash(),
        role="user",
        status=status,
        password_changed_at=now,
        failed_login_count=failed_login_count,
        failed_login_window_started_at=failed_login_window_started_at,
        locked_until=locked_until,
        created_at=now,
        created_by="seed",
        updated_at=now,
        updated_by="seed",
    )
    async with AsyncSession(engine, expire_on_commit=False) as session:
        session.add(user)
        await session.commit()
    return user


async def _reload_user(engine: AsyncEngine, user_id: uuid.UUID) -> User:
    async with AsyncSession(engine) as session:
        return (await session.execute(select(User).where(User.id == user_id))).scalar_one()


def test_settings_expose_architecture_lockout_defaults(lockout_settings) -> None:
    assert lockout_settings.admin_login_failure_threshold == DEFAULT_THRESHOLD
    assert lockout_settings.admin_login_failure_window_seconds == DEFAULT_WINDOW_SECONDS
    assert lockout_settings.admin_lock_seconds == DEFAULT_LOCK_SECONDS


@pytest.mark.asyncio
@pytest.mark.parametrize("live_db", ["postgres", "mysql"], indirect=True)
async def test_prepare_allows_password_verify_for_active_user_without_resetting_counts(
    live_db, lockout_api
) -> None:
    engine, _ = live_db
    prepare_login_attempt, _, _, _ = lockout_api
    window_started = datetime.now(UTC) - timedelta(seconds=30)
    user = await _seed_user(
        engine,
        username="active-gate",
        failed_login_count=2,
        failed_login_window_started_at=window_started,
    )

    async with AsyncSession(engine) as session, session.begin():
        decision = await prepare_login_attempt(session, user.id)

    assert decision.may_verify_password is True
    assert decision.denial_reason is None
    reloaded = await _reload_user(engine, user.id)
    assert reloaded.status == "active"
    assert reloaded.failed_login_count == 2
    assert _as_utc(reloaded.failed_login_window_started_at) == _as_utc(window_started)


@pytest.mark.asyncio
@pytest.mark.parametrize("live_db", ["postgres", "mysql"], indirect=True)
async def test_first_failure_on_active_user_creates_observation_window(
    live_db, lockout_api
) -> None:
    engine, _ = live_db
    _, record_login_failure, _, _ = lockout_api
    user = await _seed_user(engine, username="first-fail")
    before = datetime.now(UTC)

    async with AsyncSession(engine) as session, session.begin():
        await record_login_failure(session, user.id)

    after = datetime.now(UTC)
    reloaded = await _reload_user(engine, user.id)
    assert reloaded.status == "active"
    assert reloaded.failed_login_count == 1
    window_started = _as_utc(reloaded.failed_login_window_started_at)
    assert window_started is not None
    assert before - timedelta(seconds=LOCK_UNTIL_TOLERANCE_SECONDS) <= window_started
    assert window_started <= after + timedelta(seconds=LOCK_UNTIL_TOLERANCE_SECONDS)
    assert reloaded.locked_until is None


@pytest.mark.asyncio
@pytest.mark.parametrize("live_db", ["postgres", "mysql"], indirect=True)
async def test_failures_below_threshold_keep_status_active(live_db, lockout_api) -> None:
    engine, _ = live_db
    _, record_login_failure, _, _ = lockout_api
    user = await _seed_user(engine, username="below-threshold")

    for _ in range(DEFAULT_THRESHOLD - 1):
        async with AsyncSession(engine) as session, session.begin():
            await record_login_failure(session, user.id)

    reloaded = await _reload_user(engine, user.id)
    assert reloaded.status == "active"
    assert reloaded.failed_login_count == DEFAULT_THRESHOLD - 1
    assert reloaded.failed_login_window_started_at is not None
    assert reloaded.locked_until is None


@pytest.mark.asyncio
@pytest.mark.parametrize("live_db", ["postgres", "mysql"], indirect=True)
async def test_reaching_threshold_locks_user_for_configured_duration(
    live_db, lockout_api
) -> None:
    engine, _ = live_db
    _, record_login_failure, _, _ = lockout_api
    user = await _seed_user(engine, username="hits-threshold")
    before = datetime.now(UTC)

    for _ in range(DEFAULT_THRESHOLD):
        async with AsyncSession(engine) as session, session.begin():
            await record_login_failure(session, user.id)

    after = datetime.now(UTC)
    reloaded = await _reload_user(engine, user.id)
    assert reloaded.status == "locked"
    assert reloaded.failed_login_count == DEFAULT_THRESHOLD
    locked_until = _as_utc(reloaded.locked_until)
    assert locked_until is not None
    expected_min = before + timedelta(seconds=DEFAULT_LOCK_SECONDS)
    expected_max = after + timedelta(seconds=DEFAULT_LOCK_SECONDS)
    assert expected_min - timedelta(seconds=LOCK_UNTIL_TOLERANCE_SECONDS) <= locked_until
    assert locked_until <= expected_max + timedelta(seconds=LOCK_UNTIL_TOLERANCE_SECONDS)


@pytest.mark.asyncio
@pytest.mark.parametrize("live_db", ["postgres", "mysql"], indirect=True)
async def test_failure_after_observation_window_expires_restarts_count_at_one(
    live_db, lockout_api
) -> None:
    engine, _ = live_db
    _, record_login_failure, _, _ = lockout_api
    stale_window = datetime.now(UTC) - timedelta(seconds=DEFAULT_WINDOW_SECONDS + 30)
    user = await _seed_user(
        engine,
        username="window-restart",
        failed_login_count=DEFAULT_THRESHOLD - 1,
        failed_login_window_started_at=stale_window,
    )

    async with AsyncSession(engine) as session, session.begin():
        await record_login_failure(session, user.id)

    reloaded = await _reload_user(engine, user.id)
    assert reloaded.status == "active"
    assert reloaded.failed_login_count == 1
    restarted = _as_utc(reloaded.failed_login_window_started_at)
    assert restarted is not None
    assert restarted > _as_utc(stale_window)
    assert reloaded.locked_until is None


@pytest.mark.asyncio
@pytest.mark.parametrize("live_db", ["postgres", "mysql"], indirect=True)
async def test_successful_login_zeros_count_and_window_and_leaves_user_unlocked(
    live_db, lockout_api
) -> None:
    engine, _ = live_db
    _, _, record_login_success, _ = lockout_api
    user = await _seed_user(
        engine,
        username="success-reset",
        failed_login_count=3,
        failed_login_window_started_at=datetime.now(UTC) - timedelta(seconds=10),
        locked_until=datetime.now(UTC) + timedelta(seconds=60),
        status="active",
    )

    async with AsyncSession(engine) as session, session.begin():
        await record_login_success(session, user.id)

    reloaded = await _reload_user(engine, user.id)
    assert reloaded.status == "active"
    assert reloaded.failed_login_count == 0
    assert reloaded.failed_login_window_started_at is None
    assert reloaded.locked_until is None


@pytest.mark.asyncio
@pytest.mark.parametrize("live_db", ["postgres", "mysql"], indirect=True)
async def test_currently_locked_user_is_rejected_without_mutating_lock_state(
    live_db, lockout_api
) -> None:
    engine, _ = live_db
    prepare_login_attempt, _, _, _ = lockout_api
    locked_until = datetime.now(UTC) + timedelta(seconds=DEFAULT_LOCK_SECONDS)
    window_started = datetime.now(UTC) - timedelta(seconds=20)
    user = await _seed_user(
        engine,
        username="still-locked",
        status="locked",
        failed_login_count=DEFAULT_THRESHOLD,
        failed_login_window_started_at=window_started,
        locked_until=locked_until,
    )

    async with AsyncSession(engine) as session, session.begin():
        decision = await prepare_login_attempt(session, user.id)

    assert decision.may_verify_password is False
    assert decision.denial_reason == "locked"
    reloaded = await _reload_user(engine, user.id)
    assert reloaded.status == "locked"
    assert reloaded.failed_login_count == DEFAULT_THRESHOLD
    assert _as_utc(reloaded.failed_login_window_started_at) == _as_utc(window_started)
    original_until = _as_utc(locked_until)
    stored_until = _as_utc(reloaded.locked_until)
    assert stored_until is not None
    assert original_until is not None
    assert abs((stored_until - original_until).total_seconds()) < 2


@pytest.mark.asyncio
@pytest.mark.parametrize("live_db", ["postgres", "mysql"], indirect=True)
async def test_expired_lock_is_cleared_to_active_before_password_attempt(
    live_db, lockout_api
) -> None:
    engine, _ = live_db
    prepare_login_attempt, record_login_failure, _, _ = lockout_api
    user = await _seed_user(
        engine,
        username="lock-expired",
        status="locked",
        failed_login_count=DEFAULT_THRESHOLD,
        failed_login_window_started_at=datetime.now(UTC) - timedelta(seconds=60),
        locked_until=datetime.now(UTC) - timedelta(seconds=5),
    )

    async with AsyncSession(engine) as session, session.begin():
        decision = await prepare_login_attempt(session, user.id)
        assert decision.may_verify_password is True
        assert decision.denial_reason is None
        assert decision.user.status == "active"
        assert decision.user.failed_login_count == 0
        assert decision.user.failed_login_window_started_at is None
        assert decision.user.locked_until is None
        # The current password attempt runs *after* the reset: one failure
        # must start a fresh window at count 1, not immediately re-lock.
        await record_login_failure(session, user.id)

    reloaded = await _reload_user(engine, user.id)
    assert reloaded.status == "active"
    assert reloaded.failed_login_count == 1
    assert reloaded.failed_login_window_started_at is not None
    assert reloaded.locked_until is None


@pytest.mark.asyncio
@pytest.mark.parametrize("live_db", ["postgres", "mysql"], indirect=True)
async def test_disabled_user_is_not_reactivated_when_lock_has_expired(
    live_db, lockout_api
) -> None:
    engine, _ = live_db
    prepare_login_attempt, _, _, _ = lockout_api
    user = await _seed_user(
        engine,
        username="disabled-expired-lock",
        status="disabled",
        failed_login_count=DEFAULT_THRESHOLD,
        failed_login_window_started_at=datetime.now(UTC) - timedelta(seconds=120),
        locked_until=datetime.now(UTC) - timedelta(seconds=5),
    )

    async with AsyncSession(engine) as session, session.begin():
        decision = await prepare_login_attempt(session, user.id)

    assert decision.may_verify_password is False
    assert decision.denial_reason == "disabled"
    reloaded = await _reload_user(engine, user.id)
    assert reloaded.status == "disabled"
    assert reloaded.failed_login_count == DEFAULT_THRESHOLD


@pytest.mark.asyncio
@pytest.mark.parametrize("live_db", ["postgres", "mysql"], indirect=True)
async def test_disabled_user_is_rejected_while_still_locked(live_db, lockout_api) -> None:
    engine, _ = live_db
    prepare_login_attempt, _, _, _ = lockout_api
    user = await _seed_user(
        engine,
        username="disabled-still-locked",
        status="disabled",
        failed_login_count=DEFAULT_THRESHOLD,
        locked_until=datetime.now(UTC) + timedelta(seconds=DEFAULT_LOCK_SECONDS),
    )

    async with AsyncSession(engine) as session, session.begin():
        decision = await prepare_login_attempt(session, user.id)

    assert decision.may_verify_password is False
    assert decision.denial_reason == "disabled"
    reloaded = await _reload_user(engine, user.id)
    assert reloaded.status == "disabled"


@pytest.mark.asyncio
@pytest.mark.parametrize("live_db", ["postgres", "mysql"], indirect=True)
async def test_concurrent_failed_logins_do_not_drop_failure_counts(
    live_db, lockout_api
) -> None:
    """Two racing failures against the same row must both persist (count 2)."""
    engine, _ = live_db
    _, record_login_failure, _, _ = lockout_api
    user = await _seed_user(engine, username="race-failures")
    barrier = asyncio.Barrier(2)

    async def fail_once() -> None:
        async with AsyncSession(engine) as session, session.begin():
            await barrier.wait()
            await record_login_failure(session, user.id)

    results = await asyncio.gather(fail_once(), fail_once(), return_exceptions=True)
    errors = [result for result in results if isinstance(result, BaseException)]
    assert not errors, results

    reloaded = await _reload_user(engine, user.id)
    assert reloaded.failed_login_count == 2
    assert reloaded.status == "active"


@pytest.mark.asyncio
@pytest.mark.parametrize("live_db", ["postgres", "mysql"], indirect=True)
async def test_overridden_threshold_and_lock_seconds_are_honored(
    live_db, lockout_api, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, _ = live_db
    _, record_login_failure, _, _ = lockout_api
    overridden_threshold = 3
    overridden_lock = 60
    monkeypatch.setenv(
        "LITEMCP_ADMIN_LOGIN_FAILURE_THRESHOLD", str(overridden_threshold)
    )
    monkeypatch.setenv("LITEMCP_ADMIN_LOCK_SECONDS", str(overridden_lock))

    from litemcp.core.config import get_settings

    get_settings.cache_clear()
    try:
        user = await _seed_user(engine, username="override-policy")
        before = datetime.now(UTC)
        for _ in range(overridden_threshold):
            async with AsyncSession(engine) as session, session.begin():
                await record_login_failure(session, user.id)
        after = datetime.now(UTC)
    finally:
        get_settings.cache_clear()

    reloaded = await _reload_user(engine, user.id)
    assert reloaded.status == "locked"
    assert reloaded.failed_login_count == overridden_threshold
    locked_until = _as_utc(reloaded.locked_until)
    assert locked_until is not None
    expected_min = before + timedelta(seconds=overridden_lock)
    expected_max = after + timedelta(seconds=overridden_lock)
    assert expected_min - timedelta(seconds=LOCK_UNTIL_TOLERANCE_SECONDS) <= locked_until
    assert locked_until <= expected_max + timedelta(seconds=LOCK_UNTIL_TOLERANCE_SECONDS)
    # Must not have used the 900-second architecture default.
    default_lock_min = before + timedelta(seconds=DEFAULT_LOCK_SECONDS - 30)
    assert locked_until < default_lock_min
