"""Contract tests for the Alembic migration system (M1-DB-003).

The migration repository must have exactly one head, and an empty database
must be upgradable to the latest revision (``upgrade head``) on both first-tier
dialects: PostgreSQL 14+ and MySQL 8.0+.

Contract fixed by this file:

* ``backend/alembic.ini`` configures the migration scripts.
* ``backend/migrations/`` holds the revision scripts (``env.py``,
  ``script.py.mako``) and the base (root) revision.
* ``alembic.ini``'s ``script_location`` points at ``backend/migrations/``.
* The revision graph has exactly one head.
* A fresh ``upgrade head`` on an empty, dedicated database succeeds on
  PostgreSQL and MySQL and leaves ``alembic_version`` at the single head.

Domain-entity tables are deliberately NOT part of this feature; the
M1-MODEL-* features add them later.
"""

import os
import threading
import time
from pathlib import Path

import aiomysql
import asyncpg
import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy.engine import make_url

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
# The litemcp user only owns the litemcp.* database on MySQL, so a privileged
# account is required to create/drop the dedicated empty test database.
MYSQL_ROOT_URL = os.environ.get(
    "LITEMCP_TEST_MYSQL_ROOT_URL",
    "mysql+aiomysql://root:litemcp-root@localhost:3307/mysql",
)


def _make_config() -> Config:
    assert ALEMBIC_INI.is_file(), f"missing Alembic config: {ALEMBIC_INI}"
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    return cfg


def _single_head() -> str:
    """Return the single head revision, failing the contract otherwise."""
    script = ScriptDirectory.from_config(_make_config())
    heads = script.get_heads()
    assert len(heads) == 1, (
        f"expected exactly one migration head, found {len(heads)}: {sorted(heads)}"
    )
    return heads[0]


def _run_upgrade_in_thread(cfg: Config, url: str) -> None:
    """Run ``upgrade head`` against ``url`` in a worker thread.

    The standard async Alembic env.py drives its engine with ``asyncio.run()``,
    which cannot be called from the pytest-asyncio event loop already running in
    this thread. A dedicated thread has no running loop, so the cookbook env.py
    runs unchanged.
    """
    cfg.set_main_option("sqlalchemy.url", url)
    error: BaseException | None = None

    def _upgrade() -> None:
        nonlocal error
        try:
            command.upgrade(cfg, "head")
        except Exception as exc:  # noqa: BLE001 - surfaced in the caller thread
            error = exc

    thread = threading.Thread(target=_upgrade, name="alembic-upgrade-head")
    thread.start()
    thread.join()
    if error is not None:
        raise error


def _url_with_database(base_url: str, database: str) -> str:
    return make_url(base_url).set(database=database).render_as_string(
        hide_password=False
    )


def _unique_database_name(prefix: str) -> str:
    return f"{prefix}_{time.time_ns()}_{os.getpid()}"


def _resolve_script_location(cfg: Config) -> Path:
    raw = cfg.get_main_option("script_location")
    assert raw is not None and raw.strip(), (
        f"{cfg.config_file_name} must define script_location"
    )
    ini_dir = Path(cfg.config_file_name).resolve().parent
    candidate = Path(raw.replace("%(here)s", str(ini_dir)))
    if not candidate.is_absolute():
        candidate = ini_dir / candidate
    return candidate.resolve()


# ---------------------------------------------------------------------------
# Migration-repo layout and single-head contract (no database required).
# ---------------------------------------------------------------------------


def test_migration_repo_layout() -> None:
    assert ALEMBIC_INI.is_file(), (
        f"Alembic config missing: expected {ALEMBIC_INI}"
    )
    assert MIGRATIONS_DIR.is_dir(), (
        f"Migration scripts directory missing: expected {MIGRATIONS_DIR}"
    )
    assert (MIGRATIONS_DIR / "env.py").is_file(), (
        f"env.py missing in {MIGRATIONS_DIR}"
    )
    assert (MIGRATIONS_DIR / "script.py.mako").is_file(), (
        f"script.py.mako missing in {MIGRATIONS_DIR}"
    )


def test_alembic_ini_points_at_migrations_dir() -> None:
    cfg = Config(str(ALEMBIC_INI))
    assert _resolve_script_location(cfg) == MIGRATIONS_DIR.resolve(), (
        f"{ALEMBIC_INI} script_location must point at {MIGRATIONS_DIR}"
    )


def test_revision_graph_has_exactly_one_head() -> None:
    script = ScriptDirectory.from_config(_make_config())
    heads = script.get_heads()
    assert len(heads) == 1, (
        f"expected exactly one migration head, found {len(heads)}: {sorted(heads)}"
    )


# ---------------------------------------------------------------------------
# Fresh ``upgrade head`` on an empty, dedicated database, per dialect.
# ---------------------------------------------------------------------------


def _parse_url(base_url: str) -> dict:
    url = make_url(base_url)
    return {
        "host": url.host,
        "port": url.port,
        "user": url.username,
        "password": url.password,
        "database": url.database,
    }


async def _pg_create_database(params, name) -> None:
    conn = await asyncpg.connect(**params)
    try:
        await conn.execute(f'CREATE DATABASE "{name}"')
    finally:
        await conn.close()


async def _pg_drop_database(params, name) -> None:
    conn = await asyncpg.connect(**params)
    try:
        await conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    finally:
        await conn.close()


async def _assert_pg_schema_at_head(params, database, head) -> None:
    conn = await asyncpg.connect(
        host=params["host"],
        port=params["port"],
        user=params["user"],
        password=params["password"],
        database=database,
    )
    try:
        row = await conn.fetchrow(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = 'alembic_version'"
        )
        assert row is not None, "alembic_version table missing after upgrade head"
        version_num = await conn.fetchval("SELECT version_num FROM alembic_version")
        assert version_num == head, (
            f"alembic_version is at {version_num}, expected head {head}"
        )
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_postgres_empty_database_upgrades_to_head() -> None:
    head = _single_head()
    params = _parse_url(POSTGRES_URL)
    database = _unique_database_name("litemcp_migrations")
    await _pg_create_database(params, database)
    try:
        _run_upgrade_in_thread(
            _make_config(), _url_with_database(POSTGRES_URL, database)
        )
        await _assert_pg_schema_at_head(params, database, head)
    finally:
        await _pg_drop_database(params, database)


def _mysql_kwargs(url: str, database: str | None = None) -> dict:
    parsed = make_url(url)
    return {
        "host": parsed.host,
        "port": parsed.port or 3306,
        "user": parsed.username,
        "password": parsed.password,
        "db": database if database is not None else parsed.database,
        "autocommit": True,
    }


async def _mysql_create_database(admin_url: str, name: str, app_user: str) -> None:
    conn = await aiomysql.connect(**_mysql_kwargs(admin_url))
    try:
        async with conn.cursor() as cur:
            await cur.execute(f"CREATE DATABASE IF NOT EXISTS `{name}`")
            safe_user = app_user.replace("'", "''")
            await cur.execute(
                f"GRANT ALL PRIVILEGES ON `{name}`.* TO '{safe_user}'@'%'"
            )
    finally:
        conn.close()


async def _mysql_drop_database(admin_url: str, name: str) -> None:
    conn = await aiomysql.connect(**_mysql_kwargs(admin_url))
    try:
        async with conn.cursor() as cur:
            await cur.execute(f"DROP DATABASE IF EXISTS `{name}`")
    finally:
        conn.close()


async def _assert_mysql_schema_at_head(app_url: str, database: str, head: str) -> None:
    conn = await aiomysql.connect(**_mysql_kwargs(app_url, database=database))
    try:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT COUNT(*) FROM information_schema.tables "
                "WHERE table_schema = %s AND table_name = 'alembic_version'",
                (database,),
            )
            (count,) = await cur.fetchone()
            assert count == 1, "alembic_version table missing after upgrade head"
            await cur.execute("SELECT version_num FROM alembic_version")
            (version_num,) = await cur.fetchone()
            assert version_num == head, (
                f"alembic_version is at {version_num}, expected head {head}"
            )
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_mysql_empty_database_upgrades_to_head() -> None:
    head = _single_head()
    app_user = make_url(MYSQL_URL).username
    database = _unique_database_name("litemcp_migrations")
    await _mysql_create_database(MYSQL_ROOT_URL, database, app_user)
    try:
        _run_upgrade_in_thread(
            _make_config(), _url_with_database(MYSQL_URL, database)
        )
        await _assert_mysql_schema_at_head(MYSQL_URL, database, head)
    finally:
        await _mysql_drop_database(MYSQL_ROOT_URL, database)
