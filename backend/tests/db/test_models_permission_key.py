"""DB-layer contract suite for M1-MODEL-007 (mcp_service_permission + api_key).

Pins the documented schema of ``docs/architecture/01-data-model.md`` §5.12
(mcp_service_permission) and §5.13 (api_key), together with the §3.2 generic
audit-field convention and the §2/§3.1 constraint conventions.

Two sections:

* OFFLINE structural checks against ``Base.metadata`` (no database): exact
  column sets, portable types, nullability, PK/FK/UNIQUE/CHECK constraints and
  the absence of soft-delete columns.
* LIVE tests parametrized over real PostgreSQL and MySQL. Each live test
  provisions a UNIQUE named empty database, runs ``alembic upgrade head`` on
  it, drives the tables through an async engine, and drops the database in
  teardown so runs never collide and never depend on pre-existing state.

The verification command is ``make test-db-contract TEST=models_permission_key``.
"""

import asyncio
import hashlib
import os
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
import pytest_asyncio
import sqlalchemy
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import create_async_engine

from litemcp.db.models import Base
from litemcp.db.types import ENUM_CODE, ID, UTC_TS

BACKEND_DIR = Path(__file__).resolve().parents[2]

DEFAULT_POSTGRES_URL = "postgresql+asyncpg://litemcp:litemcp@localhost:5433/litemcp"
DEFAULT_MYSQL_URL = "mysql+aiomysql://litemcp:litemcp@localhost:3307/litemcp"
DEFAULT_MYSQL_ROOT_URL = "mysql+aiomysql://root:litemcp-root@localhost:3307/mysql"

POSTGRES_URL = os.environ.get("LITEMCP_TEST_POSTGRES_URL", DEFAULT_POSTGRES_URL)
MYSQL_URL = os.environ.get("LITEMCP_TEST_MYSQL_URL", DEFAULT_MYSQL_URL)
MYSQL_ROOT_URL = os.environ.get("LITEMCP_TEST_MYSQL_ROOT_URL", DEFAULT_MYSQL_ROOT_URL)

_TABLE_PERMISSION = "mcp_service_permission"
_TABLE_API_KEY = "api_key"

# Full §3.2 audit set (no soft-delete on either table).
_AUDIT_COLUMNS = ("created_at", "created_by", "updated_at", "updated_by", "row_version")

_PERMISSION_COLUMNS = {
    "id",
    "service_id",
    "principal_type",
    "user_id",
    "team_id",
    "role",
    "principal_key",
    *_AUDIT_COLUMNS,
}

_API_KEY_COLUMNS = {
    "id",
    "service_id",
    "public_id",
    "display_prefix",
    "secret_hash",
    "hash_algorithm",
    "pepper_version",
    "name",
    "status",
    "expires_at",
    "last_used_at",
    "last_used_ip_hash",
    "revoked_at",
    "revoked_by",
    "rate_limit_qps",
    "rate_limit_burst",
    *_AUDIT_COLUMNS,
}

# Canonical (whitespace-normalized) CHECK texts pinned from §5.12/§5.13.
_PERMISSION_USER_ID_CHECK = (
    "(principal_type='user'anduser_idisnotnull)or"
    "(principal_type!='user'anduser_idisnull)"
)
_PERMISSION_TEAM_ID_CHECK = (
    "(principal_type='team'andteam_idisnotnull)or"
    "(principal_type!='team'andteam_idisnull)"
)
_PERMISSION_ROLE_SCOPE_CHECK = "(principal_type='user')or(role='viewer')"
_API_KEY_EXPIRES_CHECK = "expires_atisnullorexpires_at>created_at"
_API_KEY_REVOKED_CHECK = "(status!='revoked')or(revoked_atisnotnull)"

# Nullable numeric checks may be written with or without the NULL guard.
_API_KEY_QPS_CHECKS = {
    "rate_limit_qpsisnullorrate_limit_qps>0",
    "rate_limit_qps>0",
}
_API_KEY_BURST_CHECKS = {
    "rate_limit_burstisnullorrate_limit_burst>=1",
    "rate_limit_burst>=1",
}


# ---------------------------------------------------------------------------
# Offline (no-database) helpers
# ---------------------------------------------------------------------------
def _table(name: str) -> sqlalchemy.Table:
    try:
        return Base.metadata.tables[name]
    except KeyError:
        pytest.fail(f"table {name!r} is not registered on Base.metadata")


def _strip_outer_parens(sql_text: str) -> str:
    while len(sql_text) >= 2 and sql_text.startswith("("):
        depth = 0
        end = -1
        for i, ch in enumerate(sql_text):
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    end = i
                    break
        if end == len(sql_text) - 1:
            sql_text = sql_text[1:-1]
        else:
            break
    return sql_text


def _normalize(sql_text: str) -> str:
    """Lowercase, strip whitespace, unify not-equal spellings and outer parens."""
    compact = "".join(sql_text.lower().split()).replace("<>", "!=")
    return _strip_outer_parens(compact)


def _check_expressions(table: sqlalchemy.Table) -> list[str]:
    return [
        _normalize(str(c.sqltext))
        for c in table.constraints
        if isinstance(c, sqlalchemy.CheckConstraint)
    ]


def _has_check(table: sqlalchemy.Table, expected: str) -> bool:
    return expected in _check_expressions(table)


def _has_any_check(table: sqlalchemy.Table, candidates: set[str]) -> bool:
    return any(candidate in candidates for candidate in _check_expressions(table))


def _enum_check(table: sqlalchemy.Table, column: str, allowed: set[str]) -> bool:
    for c in table.constraints:
        if not isinstance(c, sqlalchemy.CheckConstraint):
            continue
        raw = str(c.sqltext)
        if column not in raw:
            continue
        if set(re.findall(r"'([^']*)'", raw)) == allowed:
            return True
    return False


def _fk_targets(table: sqlalchemy.Table) -> dict[str, tuple[str, str]]:
    return {
        fk.parent.name: (fk.column.table.name, fk.column.name)
        for fk in table.foreign_keys
    }


def _unique_sets(table: sqlalchemy.Table) -> set[frozenset[str]]:
    unique: set[frozenset[str]] = set()
    for con in table.constraints:
        if isinstance(con, sqlalchemy.UniqueConstraint):
            unique.add(frozenset(col.name for col in con.columns))
    for index in table.indexes:
        if index.unique:
            unique.add(frozenset(col.name for col in index.columns))
    return unique


def _assert_string(table: sqlalchemy.Table, name: str, length: int) -> None:
    col = table.c[name]
    assert isinstance(col.type, sqlalchemy.String), f"{name} type is {col.type!r}"
    assert col.type.length == length, f"{name} length is {col.type.length!r}"


def _assert_audit_set(table: sqlalchemy.Table) -> None:
    assert isinstance(table.c.created_at.type, UTC_TS)
    assert not table.c.created_at.nullable
    assert isinstance(table.c.updated_at.type, UTC_TS)
    assert not table.c.updated_at.nullable
    for name in ("created_by", "updated_by"):
        _assert_string(table, name, 128)
        assert not table.c[name].nullable
    assert isinstance(table.c.row_version.type, sqlalchemy.Integer)
    assert not table.c.row_version.nullable
    # No soft-delete on either table.
    assert "deleted_at" not in table.c
    assert "deleted_by" not in table.c
    assert "uniqueness_scope" not in table.c


# ---------------------------------------------------------------------------
# Offline structural tests
# ---------------------------------------------------------------------------
def test_mcp_service_permission_structure() -> None:
    t = _table(_TABLE_PERMISSION)
    assert set(t.columns.keys()) == _PERMISSION_COLUMNS
    assert set(t.primary_key.columns.keys()) == {"id"}

    assert isinstance(t.c.id.type, ID)
    assert not t.c.id.nullable

    assert isinstance(t.c.service_id.type, ID)
    assert not t.c.service_id.nullable
    assert isinstance(t.c.user_id.type, ID)
    assert t.c.user_id.nullable
    assert isinstance(t.c.team_id.type, ID)
    assert t.c.team_id.nullable

    assert isinstance(t.c.principal_type.type, ENUM_CODE)
    assert not t.c.principal_type.nullable
    assert isinstance(t.c.role.type, ENUM_CODE)
    assert not t.c.role.nullable

    _assert_string(t, "principal_key", 80)
    assert not t.c.principal_key.nullable

    _assert_audit_set(t)


def test_api_key_structure() -> None:
    t = _table(_TABLE_API_KEY)
    assert set(t.columns.keys()) == _API_KEY_COLUMNS
    assert set(t.primary_key.columns.keys()) == {"id"}

    assert isinstance(t.c.id.type, ID)
    assert not t.c.id.nullable

    assert isinstance(t.c.service_id.type, ID)
    assert not t.c.service_id.nullable
    assert isinstance(t.c.revoked_by.type, ID)
    assert t.c.revoked_by.nullable

    assert isinstance(t.c.status.type, ENUM_CODE)
    assert not t.c.status.nullable

    _assert_string(t, "public_id", 32)
    _assert_string(t, "display_prefix", 32)
    _assert_string(t, "secret_hash", 64)
    _assert_string(t, "hash_algorithm", 32)
    _assert_string(t, "pepper_version", 64)
    _assert_string(t, "name", 128)
    _assert_string(t, "last_used_ip_hash", 64)
    assert not t.c.public_id.nullable
    assert not t.c.display_prefix.nullable
    assert not t.c.secret_hash.nullable
    assert not t.c.hash_algorithm.nullable
    assert t.c.pepper_version.nullable
    assert not t.c.name.nullable
    assert t.c.last_used_ip_hash.nullable

    for name in ("expires_at", "last_used_at", "revoked_at"):
        assert isinstance(t.c[name].type, UTC_TS)
        assert t.c[name].nullable

    assert isinstance(t.c.rate_limit_qps.type, sqlalchemy.Numeric)
    assert t.c.rate_limit_qps.nullable
    assert isinstance(t.c.rate_limit_burst.type, sqlalchemy.Integer)
    assert t.c.rate_limit_burst.nullable

    _assert_audit_set(t)


def test_mcp_service_permission_check_constraints() -> None:
    t = _table(_TABLE_PERMISSION)
    assert _enum_check(t, "principal_type", {"user", "team", "everyone"})
    assert _enum_check(t, "role", {"editor", "viewer"})
    assert _has_check(t, _PERMISSION_USER_ID_CHECK)
    assert _has_check(t, _PERMISSION_TEAM_ID_CHECK)
    assert _has_check(t, _PERMISSION_ROLE_SCOPE_CHECK)


def test_api_key_check_constraints() -> None:
    t = _table(_TABLE_API_KEY)
    assert _enum_check(t, "status", {"active", "revoked"})
    assert _has_check(t, _API_KEY_EXPIRES_CHECK)
    assert _has_check(t, _API_KEY_REVOKED_CHECK)
    assert _has_any_check(t, _API_KEY_QPS_CHECKS)
    assert _has_any_check(t, _API_KEY_BURST_CHECKS)


def test_mcp_service_permission_keys_and_fks() -> None:
    t = _table(_TABLE_PERMISSION)
    fks = _fk_targets(t)
    assert fks["service_id"] == ("mcp_service", "id")
    assert fks["user_id"] == ("user", "id")
    assert fks["team_id"] == ("team", "id")
    assert frozenset({"service_id", "principal_key"}) in _unique_sets(t)


def test_api_key_keys_and_fks() -> None:
    t = _table(_TABLE_API_KEY)
    fks = _fk_targets(t)
    assert fks["service_id"] == ("mcp_service", "id")
    assert fks["revoked_by"] == ("user", "id")
    uniques = _unique_sets(t)
    assert frozenset({"public_id"}) in uniques
    assert frozenset({"secret_hash"}) in uniques


# ---------------------------------------------------------------------------
# Live-dialect infrastructure
# ---------------------------------------------------------------------------
class _LiveDB:
    def __init__(self, dialect: str, engine: sqlalchemy.ext.asyncio.AsyncEngine) -> None:
        self.dialect = dialect
        self.engine = engine


_LIVE_DIALECTS = [
    pytest.param(("postgres", POSTGRES_URL), id="postgres"),
    pytest.param(("mysql", MYSQL_URL), id="mysql"),
]


def _maintenance_url(url: str, database: str) -> str:
    parsed = make_url(url)
    # str(URL) masks the password; render with hide_password=False so the
    # engine connects with the real credentials (matches the other contract
    # suites' helper pattern).
    return parsed.set(database=database).render_as_string(hide_password=False)


async def _create_database(dialect: str, name: str) -> None:
    if dialect == "postgres":
        engine = create_async_engine(
            _maintenance_url(POSTGRES_URL, "postgres"), isolation_level="AUTOCOMMIT"
        )
        try:
            async with engine.connect() as conn:
                await conn.execute(text(f"CREATE DATABASE {name}"))
        finally:
            await engine.dispose()
    else:
        engine = create_async_engine(MYSQL_ROOT_URL, isolation_level="AUTOCOMMIT")
        try:
            async with engine.connect() as conn:
                await conn.execute(text(f"CREATE DATABASE {name} CHARACTER SET utf8mb4"))
                # Grant only to hosts the litemcp user already exists on: MySQL 8
                # refuses to auto-create a user via GRANT (errno 1410), and
                # exec_driver_sql + %-escaping avoids aiomysql's %-formatting.
                result = await conn.exec_driver_sql(
                    "SELECT host FROM mysql.user WHERE user = 'litemcp'"
                )
                hosts = [row[0] for row in result.fetchall()]
                for host in hosts or ["localhost"]:
                    safe_host = str(host).replace("%", "%%")
                    await conn.exec_driver_sql(
                        f"GRANT ALL PRIVILEGES ON `{name}`.* TO 'litemcp'@'{safe_host}'"
                    )
                await conn.execute(text("FLUSH PRIVILEGES"))
        finally:
            await engine.dispose()


async def _drop_database(dialect: str, name: str) -> None:
    if dialect == "postgres":
        engine = create_async_engine(
            _maintenance_url(POSTGRES_URL, "postgres"), isolation_level="AUTOCOMMIT"
        )
        try:
            async with engine.connect() as conn:
                await conn.execute(text(f"DROP DATABASE IF EXISTS {name}"))
        finally:
            await engine.dispose()
    else:
        engine = create_async_engine(MYSQL_ROOT_URL, isolation_level="AUTOCOMMIT")
        try:
            async with engine.connect() as conn:
                await conn.execute(text(f"DROP DATABASE IF EXISTS {name}"))
        finally:
            await engine.dispose()


def _upgrade_head(url: str) -> None:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")


@pytest_asyncio.fixture(params=_LIVE_DIALECTS)
async def live_db(request: pytest.FixtureRequest) -> _LiveDB:
    dialect, app_url = request.param
    name = f"mcp_permkey_{uuid.uuid4().hex[:12]}"
    await _create_database(dialect, name)
    url = _maintenance_url(app_url, name)
    engine = None
    try:
        # env.py drives its engine with asyncio.run(), which cannot run inside the
        # pytest-asyncio event-loop thread, so upgrade on a dedicated worker thread.
        await asyncio.to_thread(_upgrade_head, url)
        engine = create_async_engine(url)
        yield _LiveDB(dialect=dialect, engine=engine)
    finally:
        if engine is not None:
            await engine.dispose()
        await _drop_database(dialect, name)


def _timestamp() -> datetime:
    return datetime.now(UTC)


def _team_values(team_id: uuid.UUID, now: datetime) -> dict:
    return {
        "id": team_id,
        "key": f"t{uuid.uuid4().hex[:12]}",
        "key_normalized": f"t{uuid.uuid4().hex[:12]}",
        "name": "Contract Team",
        "status": "active",
        "created_at": now,
        "created_by": "test",
        "updated_at": now,
        "updated_by": "test",
        "row_version": 1,
    }


def _user_values(user_id: uuid.UUID, now: datetime) -> dict:
    return {
        "id": user_id,
        "username": f"u{uuid.uuid4().hex[:12]}",
        "username_normalized": f"u{uuid.uuid4().hex[:12]}",
        "password_hash": "contract-test-hash",
        "role": "user",
        "status": "active",
        "password_changed_at": now,
        "failed_login_count": 0,
        "created_at": now,
        "created_by": "test",
        "updated_at": now,
        "updated_by": "test",
        "row_version": 1,
    }


def _service_values(service_id: uuid.UUID, team_id: uuid.UUID, now: datetime) -> dict:
    return {
        "id": service_id,
        "namespace_key": "default",
        "team_id": team_id,
        "type": "http_api",
        "name": f"s{uuid.uuid4().hex[:12]}",
        "name_normalized": f"s{uuid.uuid4().hex[:12]}",
        "uniqueness_scope": "LIVE",
        "tags": [],
        "desired_status": "enabled",
        "generation": 1,
        "observed_generation": 0,
        "runtime_status": "pending",
        "agent_auth_mode": "api_key",
        "created_at": now,
        "created_by": "test",
        "updated_at": now,
        "updated_by": "test",
        "row_version": 1,
    }


async def _seed_parents(conn: sqlalchemy.ext.asyncio.AsyncConnection) -> dict[str, uuid.UUID]:
    now = _timestamp()
    ids = {
        "team_id": uuid.uuid4(),
        "user_id": uuid.uuid4(),
        "service_id": uuid.uuid4(),
    }
    await conn.execute(_table("team").insert().values(_team_values(ids["team_id"], now)))
    await conn.execute(_table("user").insert().values(_user_values(ids["user_id"], now)))
    await conn.execute(
        _table("mcp_service").insert().values(
            _service_values(ids["service_id"], ids["team_id"], now)
        )
    )
    return ids


def _permission_values(
    ids: dict[str, uuid.UUID],
    *,
    principal_type: str,
    role: str,
    user_id: uuid.UUID | None = None,
    team_id: uuid.UUID | None = None,
    principal_key: str | None = None,
    service_id: uuid.UUID | None = None,
) -> dict:
    now = _timestamp()
    if principal_key is None:
        if principal_type == "user":
            principal_key = f"user:{user_id}"
        elif principal_type == "team":
            principal_key = f"team:{team_id}"
        else:
            principal_key = "everyone"
    return {
        "id": uuid.uuid4(),
        "service_id": ids["service_id"] if service_id is None else service_id,
        "principal_type": principal_type,
        "user_id": user_id,
        "team_id": team_id,
        "role": role,
        "principal_key": principal_key,
        "created_at": now,
        "created_by": "test",
        "updated_at": now,
        "updated_by": "test",
        "row_version": 1,
    }


def _api_key_values(
    ids: dict[str, uuid.UUID],
    *,
    public_id: str | None = None,
    secret_hash: str | None = None,
    status: str = "active",
    revoked_at: datetime | None = None,
    revoked_by: uuid.UUID | None = None,
    expires_at: datetime | None = None,
    created_at: datetime | None = None,
    rate_limit_qps: int | None = None,
    rate_limit_burst: int | None = None,
    service_id: uuid.UUID | None = None,
) -> dict:
    now = _timestamp()
    return {
        "id": uuid.uuid4(),
        "service_id": ids["service_id"] if service_id is None else service_id,
        "public_id": public_id if public_id is not None else f"pk_{uuid.uuid4().hex[:12]}",
        "display_prefix": uuid.uuid4().hex[:8],
        "secret_hash": (
            secret_hash
            if secret_hash is not None
            else hashlib.sha256(uuid.uuid4().bytes).hexdigest()
        ),
        "hash_algorithm": "sha256-v1",
        "name": "contract key",
        "status": status,
        "expires_at": expires_at,
        "last_used_at": None,
        "last_used_ip_hash": None,
        "revoked_at": revoked_at,
        "revoked_by": revoked_by,
        "rate_limit_qps": rate_limit_qps,
        "rate_limit_burst": rate_limit_burst,
        "created_at": created_at if created_at is not None else now,
        "created_by": "test",
        "updated_at": now,
        "updated_by": "test",
        "row_version": 1,
    }


async def _row_count(conn: sqlalchemy.ext.asyncio.AsyncConnection, table: sqlalchemy.Table) -> int:
    result = await conn.execute(sqlalchemy.select(sqlalchemy.func.count()).select_from(table))
    return result.scalar_one()


# ---------------------------------------------------------------------------
# Live: mcp_service_permission
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_permission_valid_rows_persist(live_db: _LiveDB) -> None:
    perm = _table(_TABLE_PERMISSION)
    async with live_db.engine.begin() as conn:
        ids = await _seed_parents(conn)
        await conn.execute(
            perm.insert().values(
                _permission_values(
                    ids, principal_type="user", role="editor", user_id=ids["user_id"]
                )
            )
        )
        await conn.execute(
            perm.insert().values(
                _permission_values(
                    ids, principal_type="team", role="viewer", team_id=ids["team_id"]
                )
            )
        )
        await conn.execute(
            perm.insert().values(
                _permission_values(ids, principal_type="everyone", role="viewer")
            )
        )
        assert await _row_count(conn, perm) == 3


@pytest.mark.asyncio
async def test_permission_unique_service_principal_key(live_db: _LiveDB) -> None:
    perm = _table(_TABLE_PERMISSION)
    async with live_db.engine.begin() as conn:
        ids = await _seed_parents(conn)
        await conn.execute(
            perm.insert().values(
                _permission_values(
                    ids, principal_type="user", role="editor", user_id=ids["user_id"]
                )
            )
        )
    with pytest.raises(IntegrityError):
        async with live_db.engine.begin() as conn:
            await conn.execute(
                perm.insert().values(
                    _permission_values(
                        ids, principal_type="user", role="editor", user_id=ids["user_id"]
                    )
                )
            )


@pytest.mark.asyncio
async def test_permission_user_requires_user_id(live_db: _LiveDB) -> None:
    perm = _table(_TABLE_PERMISSION)
    async with live_db.engine.begin() as conn:
        ids = await _seed_parents(conn)
    with pytest.raises((IntegrityError, OperationalError)):
        async with live_db.engine.begin() as conn:
            await conn.execute(
                perm.insert().values(
                    _permission_values(ids, principal_type="user", role="editor")
                )
            )


@pytest.mark.asyncio
async def test_permission_non_user_forbids_user_id(live_db: _LiveDB) -> None:
    perm = _table(_TABLE_PERMISSION)
    async with live_db.engine.begin() as conn:
        ids = await _seed_parents(conn)
    with pytest.raises((IntegrityError, OperationalError)):
        async with live_db.engine.begin() as conn:
            await conn.execute(
                perm.insert().values(
                    _permission_values(
                        ids,
                        principal_type="everyone",
                        role="viewer",
                        user_id=ids["user_id"],
                    )
                )
            )


@pytest.mark.asyncio
async def test_permission_team_requires_team_id(live_db: _LiveDB) -> None:
    perm = _table(_TABLE_PERMISSION)
    async with live_db.engine.begin() as conn:
        ids = await _seed_parents(conn)
    with pytest.raises((IntegrityError, OperationalError)):
        async with live_db.engine.begin() as conn:
            await conn.execute(
                perm.insert().values(
                    _permission_values(ids, principal_type="team", role="viewer")
                )
            )


@pytest.mark.asyncio
async def test_permission_team_and_everyone_restricted_to_viewer(live_db: _LiveDB) -> None:
    perm = _table(_TABLE_PERMISSION)
    async with live_db.engine.begin() as conn:
        ids = await _seed_parents(conn)
    with pytest.raises((IntegrityError, OperationalError)):
        async with live_db.engine.begin() as conn:
            await conn.execute(
                perm.insert().values(
                    _permission_values(ids, principal_type="everyone", role="editor")
                )
            )
    with pytest.raises((IntegrityError, OperationalError)):
        async with live_db.engine.begin() as conn:
            await conn.execute(
                perm.insert().values(
                    _permission_values(
                        ids,
                        principal_type="team",
                        role="editor",
                        team_id=ids["team_id"],
                    )
                )
            )


@pytest.mark.asyncio
async def test_permission_service_fk(live_db: _LiveDB) -> None:
    perm = _table(_TABLE_PERMISSION)
    async with live_db.engine.begin() as conn:
        ids = await _seed_parents(conn)
    with pytest.raises(IntegrityError):
        async with live_db.engine.begin() as conn:
            await conn.execute(
                perm.insert().values(
                    _permission_values(
                        ids,
                        principal_type="user",
                        role="editor",
                        user_id=ids["user_id"],
                        service_id=uuid.uuid4(),
                    )
                )
            )


# ---------------------------------------------------------------------------
# Live: api_key
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_api_key_insert_and_revoke(live_db: _LiveDB) -> None:
    key = _table(_TABLE_API_KEY)
    async with live_db.engine.begin() as conn:
        ids = await _seed_parents(conn)
        await conn.execute(key.insert().values(_api_key_values(ids)))
        assert await _row_count(conn, key) == 1

        key_id = (await conn.execute(sqlalchemy.select(key.c.id))).scalar_one()
        await conn.execute(
            key.update()
            .where(key.c.id == key_id)
            .values(status="revoked", revoked_at=_timestamp(), revoked_by=ids["user_id"])
        )
        row = (
            await conn.execute(
                sqlalchemy.select(key.c.status, key.c.revoked_at, key.c.revoked_by).where(
                    key.c.id == key_id
                )
            )
        ).one()
        assert row.status == "revoked"
        assert row.revoked_at is not None
        assert row.revoked_by == ids["user_id"]


@pytest.mark.asyncio
async def test_api_key_unique_public_id(live_db: _LiveDB) -> None:
    key = _table(_TABLE_API_KEY)
    async with live_db.engine.begin() as conn:
        ids = await _seed_parents(conn)
        await conn.execute(key.insert().values(_api_key_values(ids, public_id="dup-public")))
    with pytest.raises(IntegrityError):
        async with live_db.engine.begin() as conn:
            await conn.execute(key.insert().values(_api_key_values(ids, public_id="dup-public")))


@pytest.mark.asyncio
async def test_api_key_unique_secret_hash(live_db: _LiveDB) -> None:
    key = _table(_TABLE_API_KEY)
    async with live_db.engine.begin() as conn:
        ids = await _seed_parents(conn)
        await conn.execute(key.insert().values(_api_key_values(ids, secret_hash="dup-hash")))
    with pytest.raises(IntegrityError):
        async with live_db.engine.begin() as conn:
            await conn.execute(key.insert().values(_api_key_values(ids, secret_hash="dup-hash")))


@pytest.mark.asyncio
async def test_api_key_revoked_requires_revoked_at(live_db: _LiveDB) -> None:
    key = _table(_TABLE_API_KEY)
    async with live_db.engine.begin() as conn:
        ids = await _seed_parents(conn)
    with pytest.raises((IntegrityError, OperationalError)):
        async with live_db.engine.begin() as conn:
            await conn.execute(key.insert().values(_api_key_values(ids, status="revoked")))


@pytest.mark.asyncio
async def test_api_key_expires_after_created(live_db: _LiveDB) -> None:
    key = _table(_TABLE_API_KEY)
    async with live_db.engine.begin() as conn:
        ids = await _seed_parents(conn)
    same = _timestamp()
    with pytest.raises((IntegrityError, OperationalError)):
        async with live_db.engine.begin() as conn:
            await conn.execute(
                key.insert().values(_api_key_values(ids, created_at=same, expires_at=same))
            )


@pytest.mark.asyncio
async def test_api_key_rate_limit_checks(live_db: _LiveDB) -> None:
    key = _table(_TABLE_API_KEY)
    async with live_db.engine.begin() as conn:
        ids = await _seed_parents(conn)
    with pytest.raises((IntegrityError, OperationalError)):
        async with live_db.engine.begin() as conn:
            await conn.execute(key.insert().values(_api_key_values(ids, rate_limit_qps=0)))
    with pytest.raises((IntegrityError, OperationalError)):
        async with live_db.engine.begin() as conn:
            await conn.execute(key.insert().values(_api_key_values(ids, rate_limit_burst=0)))


@pytest.mark.asyncio
async def test_api_key_fks(live_db: _LiveDB) -> None:
    key = _table(_TABLE_API_KEY)
    async with live_db.engine.begin() as conn:
        ids = await _seed_parents(conn)
    with pytest.raises(IntegrityError):
        async with live_db.engine.begin() as conn:
            await conn.execute(key.insert().values(_api_key_values(ids, service_id=uuid.uuid4())))
    with pytest.raises(IntegrityError):
        async with live_db.engine.begin() as conn:
            await conn.execute(
                key.insert().values(
                    _api_key_values(
                        ids,
                        status="revoked",
                        revoked_at=_timestamp(),
                        revoked_by=uuid.uuid4(),
                    )
                )
            )
