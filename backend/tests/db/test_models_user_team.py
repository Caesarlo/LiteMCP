"""Contract tests for the user/team/team-membership models (M1-MODEL-001).

Pins the contract of ``litemcp.db.models`` (a declarative ``Base`` plus mapped
``User``, ``Team``, ``TeamMembership``) against
docs/architecture/01-data-model.md §3.1, §3.2, §5.1, §5.16 and §5.17:

* the three tables carry the documented columns, the generic audit fields
  (``created_at`` / ``created_by`` / ``updated_at`` / ``updated_by`` /
  ``row_version``) and DB-level constraints: UNIQUE on
  ``username_normalized`` / ``key_normalized`` / ``(team_id, user_id)``, a
  portable CHECK listing every allowed enum code, and FKs from both membership
  references;
* the constraints are enforced by the DATABASE on BOTH live dialects —
  rejections surface as IntegrityError/OperationalError, never a Python-side
  value check (§3.2 L63);
* ``ID`` round-trips as the same canonical UUID, ``UTC_TS`` as the same UTC
  instant (MySQL reads re-attach UTC), ``LONG_TEXT`` exactly, and the
  ``user.status`` / ``team.status`` columns accept the documented lifecycle
  codes (``disabled`` / ``locked`` / ``archived``).

Two-dialect strategy (mirrors test_migrations.py, but the helpers are
replicated here rather than imported): for each dialect a uniquely-named EMPTY
database is created, Alembic ``upgrade head`` is run against it in a worker
thread (the async cookbook env.py needs a loop-free thread), the ORM contract
runs through an ``AsyncSessionFactory`` built on that dedicated URL, and the
database is dropped in teardown. A connection failure is a hard FAIL; there is
no SQLite fallback for this contract.
"""

from __future__ import annotations

import os
import threading
import time
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiomysql
import asyncpg
import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from litemcp.db.models import Base, Team, TeamMembership, User
from sqlalchemy import CheckConstraint, Integer, Table, UniqueConstraint, select
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError, OperationalError

from litemcp.db.session import AsyncSessionFactory
from litemcp.db.types import ID, LONG_TEXT, UTC_TS

# ---------------------------------------------------------------------------
# Env vars + defaults (same convention as test_migrations.py / test_types.py).
# ---------------------------------------------------------------------------

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
# account is required to CREATE/DROP the dedicated empty test databases.
MYSQL_ROOT_URL = os.environ.get(
    "LITEMCP_TEST_MYSQL_ROOT_URL",
    "mysql+aiomysql://root:litemcp-root@localhost:3307/mysql",
)

_DB_ERROR = (IntegrityError, OperationalError)

_TS0 = datetime(2026, 1, 2, 3, 4, 5, 123456, tzinfo=UTC)
_AUDIT: dict[str, Any] = {
    "created_at": _TS0,
    "created_by": "seed",
    "updated_at": _TS0,
    "updated_by": "seed",
    "row_version": 1,
}

# ---------------------------------------------------------------------------
# Database / Alembic machinery, replicated locally from test_migrations.py.
# ---------------------------------------------------------------------------


def _make_config() -> Config:
    assert ALEMBIC_INI.is_file(), f"missing Alembic config: {ALEMBIC_INI}"
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    return cfg


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


def _parse_url(base_url: str) -> dict[str, Any]:
    url = make_url(base_url)
    return {
        "host": url.host,
        "port": url.port,
        "user": url.username,
        "password": url.password,
        "database": url.database,
    }


async def _pg_create_database(params: dict[str, Any], name: str) -> None:
    conn = await asyncpg.connect(**params)
    try:
        await conn.execute(f'CREATE DATABASE "{name}"')
    finally:
        await conn.close()


async def _pg_drop_database(params: dict[str, Any], name: str) -> None:
    conn = await asyncpg.connect(**params)
    try:
        await conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    finally:
        await conn.close()


def _mysql_kwargs(url: str, database: str | None = None) -> dict[str, Any]:
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


# ---------------------------------------------------------------------------
# Per-dialect live environment: dedicated DB, upgrade head, ORM session factory.
# ---------------------------------------------------------------------------

_DIALECT_URLS = [("postgres", POSTGRES_URL), ("mysql", MYSQL_URL)]


@pytest_asyncio.fixture(params=_DIALECT_URLS, ids=["postgres", "mysql"])
async def model_env(
    request: pytest.FixtureRequest,
) -> AsyncIterator[AsyncSessionFactory]:
    """One live dialect: dedicated empty DB upgraded to head, then an ORM
    session factory bound to that dedicated URL. Drops the DB in teardown."""
    param = request.param
    assert isinstance(param, tuple) and len(param) == 2
    dialect_name = str(param[0])
    base_url = str(param[1])

    if dialect_name == "postgres":
        params = _parse_url(base_url)
        database = _unique_database_name("litemcp_models")
        await _pg_create_database(params, database)
        url = _url_with_database(base_url, database)
        factory = AsyncSessionFactory(url)
        try:
            _run_upgrade_in_thread(_make_config(), url)
            yield factory
        finally:
            await factory.dispose()
            await _pg_drop_database(params, database)
    else:
        app_user = make_url(base_url).username
        database = _unique_database_name("litemcp_models")
        await _mysql_create_database(MYSQL_ROOT_URL, database, app_user)
        url = _url_with_database(base_url, database)
        factory = AsyncSessionFactory(url)
        try:
            _run_upgrade_in_thread(_make_config(), url)
            yield factory
        finally:
            await factory.dispose()
            await _mysql_drop_database(MYSQL_ROOT_URL, database)


# ---------------------------------------------------------------------------
# ORM object / read helpers.
# ---------------------------------------------------------------------------


def _as_utc(value: datetime | None) -> datetime | None:
    """Re-attach UTC to MySQL's naive timestamps; PG already returns aware."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _make_user(**overrides: Any) -> User:
    values: dict[str, Any] = {
        "id": uuid.uuid4(),
        "username": "alice",
        "username_normalized": "alice",
        "password_hash": "argon2id$v=19$m=65536,t=3,p=4$dGVzdA",
        "role": "admin",
        "status": "active",
        "password_changed_at": _TS0,
        "last_login_at": None,
        "failed_login_window_started_at": None,
        "locked_until": None,
        **_AUDIT,
    }
    values.update(overrides)
    return User(**values)


def _make_team(**overrides: Any) -> Team:
    values: dict[str, Any] = {
        "id": uuid.uuid4(),
        "key": "crm",
        "key_normalized": "crm",
        "name": "CRM Team",
        "description": "D" * 5000,  # > 4 KiB, exercises TEXT/LONGTEXT
        "status": "active",
        **_AUDIT,
    }
    values.update(overrides)
    return Team(**values)


def _make_membership(team_id: uuid.UUID, user_id: uuid.UUID, **overrides: Any) -> TeamMembership:
    values: dict[str, Any] = {
        "id": uuid.uuid4(),
        "team_id": team_id,
        "user_id": user_id,
        "team_role": "member",
        **_AUDIT,
    }
    values.update(overrides)
    return TeamMembership(**values)


async def _persist(factory: AsyncSessionFactory, *objects: object) -> None:
    """Insert the ORM objects in their own session/transaction and commit."""
    async with factory.session() as s:
        await s.begin()
        s.add_all(objects)
        await s.commit()


async def _fetch_one(factory: AsyncSessionFactory, stmt: Any) -> Any:
    """Read exactly one row through a fresh session."""
    async with factory.session() as s:
        return (await s.execute(stmt)).scalar_one()


# ---------------------------------------------------------------------------
# Offline structural contract (no database required).
# ---------------------------------------------------------------------------

_USER_COLUMNS = {
    "id", "username", "username_normalized", "password_hash", "role", "status",
    "password_changed_at", "last_login_at", "failed_login_count",
    "failed_login_window_started_at", "locked_until",
    "created_at", "created_by", "updated_at", "updated_by", "row_version",
}
_TEAM_COLUMNS = {
    "id", "key", "key_normalized", "name", "description", "status",
    "created_at", "created_by", "updated_at", "updated_by", "row_version",
}
_MEMBERSHIP_COLUMNS = {
    "id", "team_id", "user_id", "team_role",
    "created_at", "created_by", "updated_at", "updated_by", "row_version",
}


def _column_length(table: Table, colname: str) -> int:
    return int(table.columns[colname].type.length)


def _unique_on(table: Table, columns: list[str]) -> bool:
    colset = set(columns)
    for con in table.constraints:
        if isinstance(con, UniqueConstraint) and {c.name for c in con.columns} == colset:
            return True
    if len(columns) == 1:
        return bool(table.columns[columns[0]].unique)
    return False


def _has_check_codes(table: Table, colname: str, codes: set[str]) -> bool:
    for con in table.constraints:
        if not isinstance(con, CheckConstraint):
            continue
        text = str(con.sqltext).lower()
        if colname in text and all(code in text for code in codes):
            return True
    return False


def _fk_target(table: Table, colname: str) -> str | None:
    col = table.columns[colname]
    for fk in col.foreign_keys:
        if fk.column is not None:
            return fk.column.table.name
    return None


def test_models_declare_contract() -> None:
    """The ORM models declare the documented tables, columns and constraints."""
    assert {"user", "team", "team_membership"} <= set(Base.metadata.tables)
    assert User.__tablename__ == "user"
    assert Team.__tablename__ == "team"
    assert TeamMembership.__tablename__ == "team_membership"

    user_cols = {c.name for c in User.__table__.columns}
    assert _USER_COLUMNS <= user_cols
    assert "deleted_at" not in user_cols  # users are disabled, never soft-deleted

    team_cols = {c.name for c in Team.__table__.columns}
    assert _TEAM_COLUMNS <= team_cols
    assert "deleted_at" not in team_cols
    # §5.16 L519: teams are archived, not soft-deleted via uniqueness_scope.
    assert "uniqueness_scope" not in team_cols

    membership_cols = {c.name for c in TeamMembership.__table__.columns}
    assert _MEMBERSHIP_COLUMNS <= membership_cols

    user_t = User.__table__
    team_t = Team.__table__
    member_t = TeamMembership.__table__

    # Column types come from the litemcp.db.types surface (§3.1).
    assert isinstance(user_t.columns["id"].type, ID)
    assert isinstance(user_t.columns["password_changed_at"].type, UTC_TS)
    assert isinstance(user_t.columns["last_login_at"].type, UTC_TS)
    assert isinstance(user_t.columns["failed_login_count"].type, Integer)
    assert isinstance(team_t.columns["description"].type, LONG_TEXT)

    assert _column_length(user_t, "username") == 128
    assert _column_length(user_t, "username_normalized") == 128
    assert _column_length(user_t, "password_hash") == 255
    assert _column_length(team_t, "key") == 64
    assert _column_length(team_t, "key_normalized") == 64
    assert _column_length(team_t, "name") == 128

    # Enum-like varchar columns: sized like varchar, enforced by a portable
    # DB CHECK listing every allowed code (never a native DB ENUM, §3.1 L46).
    for table, colname, codes in (
        (user_t, "role", {"admin", "user"}),
        (user_t, "status", {"active", "disabled", "locked"}),
        (team_t, "status", {"active", "archived"}),
        (member_t, "team_role", {"admin", "member"}),
    ):
        assert _column_length(table, colname) >= 16, colname
        assert _has_check_codes(table, colname, codes), colname

    # Unique constraints (§5.1, §5.16, §5.17).
    assert _unique_on(user_t, ["username_normalized"])
    assert _unique_on(team_t, ["key_normalized"])
    assert _unique_on(member_t, ["team_id", "user_id"])

    # Foreign keys on team_membership (§5.17).
    assert _fk_target(member_t, "team_id") == "team"
    assert _fk_target(member_t, "user_id") == "user"


# ---------------------------------------------------------------------------
# Live two-dialect contract.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_roundtrip_user_team_membership(
    model_env: AsyncSessionFactory,
) -> None:
    """ID/UTC_TS/LONG_TEXT and the audit fields round-trip on both dialects."""
    user = _make_user(last_login_at=_TS0)
    team = _make_team()
    membership = _make_membership(team_id=team.id, user_id=user.id)

    await _persist(model_env, user, team, membership)

    got_user: User = await _fetch_one(
        model_env, select(User).where(User.id == user.id)
    )
    assert isinstance(got_user.id, uuid.UUID)
    assert got_user.id == user.id
    assert got_user.username == "alice"
    assert got_user.username_normalized == "alice"
    assert got_user.role == "admin"
    assert got_user.status == "active"
    assert _as_utc(got_user.password_changed_at) == _TS0
    assert _as_utc(got_user.last_login_at) == _TS0
    assert got_user.failed_login_window_started_at is None
    assert got_user.locked_until is None
    assert got_user.failed_login_count == 0  # documented default
    assert got_user.row_version == 1
    assert got_user.created_by == "seed"
    assert got_user.updated_by == "seed"
    assert _as_utc(got_user.created_at) == _TS0
    assert _as_utc(got_user.updated_at) == _TS0

    got_team: Team = await _fetch_one(
        model_env, select(Team).where(Team.id == team.id)
    )
    assert got_team.id == team.id
    assert got_team.key == "crm"
    assert got_team.key_normalized == "crm"
    assert got_team.name == "CRM Team"
    assert got_team.description == "D" * 5000
    assert got_team.status == "active"
    assert got_team.row_version == 1
    assert _as_utc(got_team.created_at) == _TS0

    got_member: TeamMembership = await _fetch_one(
        model_env,
        select(TeamMembership).where(TeamMembership.id == membership.id),
    )
    assert isinstance(got_member.team_id, uuid.UUID)
    assert got_member.team_id == team.id
    assert got_member.user_id == user.id
    assert got_member.team_role == "member"
    assert got_member.row_version == 1


@pytest.mark.asyncio
async def test_unique_username_normalized_rejected(
    model_env: AsyncSessionFactory,
) -> None:
    """A second user with the same normalized login is rejected by the DB."""
    await _persist(model_env, _make_user())

    dup = _make_user(username="ALICE", username_normalized="alice")
    with pytest.raises(_DB_ERROR):
        await _persist(model_env, dup)


@pytest.mark.asyncio
async def test_unique_team_user_membership_rejected(
    model_env: AsyncSessionFactory,
) -> None:
    """A duplicate (team_id, user_id) membership row is rejected by the DB."""
    user = _make_user()
    team = _make_team()
    await _persist(model_env, user, team)
    await _persist(model_env, _make_membership(team_id=team.id, user_id=user.id))

    dup = _make_membership(team_id=team.id, user_id=user.id)
    with pytest.raises(_DB_ERROR):
        await _persist(model_env, dup)


@pytest.mark.asyncio
async def test_check_constraints_reject_disallowed_codes(
    model_env: AsyncSessionFactory,
) -> None:
    """Disallowed role/status/team_role codes are rejected by the DB CHECK."""
    with pytest.raises(_DB_ERROR):
        await _persist(model_env, _make_user(role="superuser"))

    with pytest.raises(_DB_ERROR):
        await _persist(model_env, _make_user(status="banned"))

    with pytest.raises(_DB_ERROR):
        await _persist(model_env, _make_team(status="deleted"))

    user = _make_user()
    team = _make_team()
    await _persist(model_env, user, team)
    bad_membership = _make_membership(
        team_id=team.id, user_id=user.id, team_role="owner"
    )
    with pytest.raises(_DB_ERROR):
        await _persist(model_env, bad_membership)


@pytest.mark.asyncio
async def test_foreign_key_rejects_orphan_membership(
    model_env: AsyncSessionFactory,
) -> None:
    """A membership referencing a missing team or user is rejected by the DB."""
    ghost = uuid.uuid4()
    with pytest.raises(_DB_ERROR):
        await _persist(
            model_env,
            _make_membership(team_id=ghost, user_id=ghost, team_role="member"),
        )

    user = _make_user()
    team = _make_team()
    await _persist(model_env, user, team)

    with pytest.raises(_DB_ERROR):
        await _persist(
            model_env,
            _make_membership(team_id=team.id, user_id=uuid.uuid4()),
        )

    with pytest.raises(_DB_ERROR):
        await _persist(
            model_env,
            _make_membership(team_id=uuid.uuid4(), user_id=user.id),
        )


@pytest.mark.asyncio
async def test_lifecycle_statuses_roundtrip(
    model_env: AsyncSessionFactory,
) -> None:
    """disabled/locked user and archived team statuses round-trip."""
    disabled = _make_user(username="bob", username_normalized="bob", status="disabled")
    locked = _make_user(
        username="carol",
        username_normalized="carol",
        status="locked",
        locked_until=_TS0,
    )
    archived = _make_team(key="legacy", key_normalized="legacy", status="archived")
    await _persist(model_env, disabled, locked, archived)

    got_disabled: User = await _fetch_one(
        model_env, select(User).where(User.username_normalized == "bob")
    )
    got_locked: User = await _fetch_one(
        model_env, select(User).where(User.username_normalized == "carol")
    )
    got_archived: Team = await _fetch_one(
        model_env, select(Team).where(Team.key_normalized == "legacy")
    )

    assert got_disabled.status == "disabled"
    assert got_locked.status == "locked"
    assert _as_utc(got_locked.locked_until) == _TS0
    assert got_archived.status == "archived"
    assert got_disabled.row_version == 1
    assert got_archived.row_version == 1
    assert got_disabled.created_by == "seed"
    assert got_archived.created_by == "seed"
    assert got_disabled.updated_by == "seed"
