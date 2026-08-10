# LiteMCP Backend

English | [简体中文](README.zh-CN.md)

FastAPI backend for [LiteMCP](../README.md) — typed configuration, health checks, and an async SQLAlchemy data layer with PostgreSQL/MySQL cross-dialect support. See the root [README](../README.md) and [architecture overview](../docs/architecture/00-overview.md) for the product-level picture; this document only covers working in `backend/`.

> [!IMPORTANT]
> Domain models (users, teams, services, toolsets), the MCP gateway, connectors, and auth are not implemented yet. `/livez` and `/readyz` are the only real endpoints today. See [`../feature_list.json`](../feature_list.json) and [`../progress.md`](../progress.md) for the authoritative, verified state.

## Requirements

- Python 3.11+
- [uv](https://docs.astral.sh/uv/)
- PostgreSQL or MySQL and Redis for anything beyond `/livez` (see [Quick start](../README.md#quick-start) in the root README for Docker Compose)

## Quick start

```bash
cd backend
uv sync
uv run uvicorn litemcp.main:app --reload
```

Configuration is loaded from environment variables with the `LITEMCP_` prefix (typed, fail-fast — see `src/litemcp/core/config.py`). Copy [`../.env.example`](../.env.example) to `.env` at the repo root, or export the required variables (`LITEMCP_DATABASE_URL`, `LITEMCP_REDIS_URL`, `LITEMCP_ENCRYPTION_KEYS`) directly.

Once running:

- `GET http://127.0.0.1:8000/livez` — process liveness only, never touches external dependencies
- `GET http://127.0.0.1:8000/readyz` — real database + Redis probes, `200` when both are healthy, `503` otherwise with a per-component breakdown

## Project layout

```
backend/
├── src/litemcp/
│   ├── core/        # typed Settings (pydantic-settings), fail-fast validation
│   ├── db/           # async session factory, cross-dialect TypeDecorators
│   ├── workers/      # placeholder worker entrypoint (python -m litemcp.workers)
│   ├── correlation.py
│   └── main.py       # FastAPI app, /livez, /readyz
├── migrations/        # Alembic environment + versions
├── tests/
│   ├── api/           # health-check contract tests
│   ├── contract/      # OpenAPI snapshot gate
│   ├── core/          # config tests
│   ├── db/             # session/types/migrations tests
│   └── middleware/
└── alembic.ini
```

## Testing and quality gates

```bash
uv run pytest                       # full backend test suite
uv run pytest tests/api/test_health.py -k livez
uv run ruff check src tests         # lint
uv run mypy src                     # type check
```

Dialect and migration contracts need a running database (see the root [`docker-compose.yml`](../docker-compose.yml)):

```bash
# from the repo root
make test-db-types     # cross-dialect type contract on live PostgreSQL + MySQL
make test-migrations   # Alembic single-head + fresh `upgrade head` on both dialects
make test-openapi      # committed OpenAPI snapshot vs. the live app.openapi()
```

## Database migrations

Alembic is configured in [`alembic.ini`](alembic.ini) / [`migrations/env.py`](migrations/env.py), async-driver aware, targeting both PostgreSQL and MySQL. To create a new revision:

```bash
uv run alembic revision -m "describe the change" --autogenerate
uv run alembic upgrade head
```

Every migration must keep a single head; `make test-migrations` enforces this on both dialects.

## Contributing

Run `uv run ruff check src tests` and `uv run mypy src` before committing. This project follows the repo-wide TDD and feature-verification workflow described in [`../AGENTS.md`](../AGENTS.md) — read it before starting non-trivial backend work.
