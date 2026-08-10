# LiteMCP 后端

[English](README.md) | 简体中文

[LiteMCP](../README.zh-CN.md) 的 FastAPI 后端——类型化配置、健康检查，以及支持 PostgreSQL/MySQL 跨方言的异步 SQLAlchemy 数据层。产品整体介绍见根目录 [README](../README.zh-CN.md) 与[架构概览](../docs/architecture/00-overview.md)；本文只覆盖在 `backend/` 目录下的开发工作。

> [!IMPORTANT]
> 领域模型（用户、团队、服务、工具集）、MCP 网关、Connector 和认证授权尚未实现。目前唯一可用的真实端点是 `/livez` 和 `/readyz`。已验证的权威状态请查看 [`../feature_list.json`](../feature_list.json) 和 [`../progress.md`](../progress.md)。

## 环境要求

- Python 3.11+
- [uv](https://docs.astral.sh/uv/)
- 除 `/livez` 外的其他功能需要 PostgreSQL 或 MySQL，以及 Redis（参见根目录 README 的[快速开始](../README.zh-CN.md#快速开始)，可用 Docker Compose 一键启动）

## 快速开始

```bash
cd backend
uv sync
uv run uvicorn litemcp.main:app --reload
```

配置通过带 `LITEMCP_` 前缀的环境变量加载（类型化、快速失败校验，见 `src/litemcp/core/config.py`）。可以将根目录的 [`../.env.example`](../.env.example) 复制为 `.env`，或直接导出所需变量（`LITEMCP_DATABASE_URL`、`LITEMCP_REDIS_URL`、`LITEMCP_ENCRYPTION_KEYS`）。

启动后可访问：

- `GET http://127.0.0.1:8000/livez`——仅反映进程存活状态，不访问任何外部依赖
- `GET http://127.0.0.1:8000/readyz`——真实探测数据库与 Redis，两者均健康时返回 `200`，否则返回 `503` 并附带每个组件的详细状态

## 目录结构

```
backend/
├── src/litemcp/
│   ├── core/        # 类型化 Settings（pydantic-settings），快速失败校验
│   ├── db/           # 异步会话工厂、跨方言 TypeDecorator
│   ├── workers/      # 占位 worker 入口（python -m litemcp.workers）
│   ├── correlation.py
│   └── main.py       # FastAPI 应用，/livez、/readyz
├── migrations/        # Alembic 环境与版本文件
├── tests/
│   ├── api/           # 健康检查契约测试
│   ├── contract/      # OpenAPI 快照门禁
│   ├── core/          # 配置测试
│   ├── db/             # session/types/migrations 测试
│   └── middleware/
└── alembic.ini
```

## 测试与质量门禁

```bash
uv run pytest                       # 完整后端测试套件
uv run pytest tests/api/test_health.py -k livez
uv run ruff check src tests         # lint
uv run mypy src                     # 类型检查
```

跨方言与迁移契约需要真实运行的数据库（参见根目录 [`docker-compose.yml`](../docker-compose.yml)）：

```bash
# 在仓库根目录执行
make test-db-types     # 在真实 PostgreSQL + MySQL 上验证跨方言类型契约
make test-migrations   # 在两种方言上验证 Alembic 单一 head 与全新 `upgrade head`
make test-openapi      # 对比已提交的 OpenAPI 快照与运行时 app.openapi()
```

## 数据库迁移

Alembic 配置见 [`alembic.ini`](alembic.ini) / [`migrations/env.py`](migrations/env.py)，支持异步驱动，同时面向 PostgreSQL 和 MySQL。创建新的迁移版本：

```bash
uv run alembic revision -m "描述这次变更" --autogenerate
uv run alembic upgrade head
```

每次迁移都必须保持单一 head；`make test-migrations` 会在两种方言上强制校验这一点。

## 参与贡献

提交前请运行 `uv run ruff check src tests` 和 `uv run mypy src`。本项目遵循 [`../AGENTS.md`](../AGENTS.md) 中描述的仓库级 TDD 与特性验证工作流，开始非平凡的后端开发前请先阅读该文档。
