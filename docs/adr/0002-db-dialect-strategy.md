# ADR-0002: 领域模型同时一级支持 PostgreSQL 14+ 与 MySQL 8.0+，通过逻辑类型层屏蔽方言差异

- Status: Accepted
- Date: 2026-08-09
- Source refs: docs/architecture/01-data-model.md §1, §3 (L9, L32-L54, L65-L75); docs/architecture/00-overview.md (L23, L141); docs/architecture/08-implementation-plan.md (L20, L89, L133, L341-L348); docs/architecture/03-service-crud.md (L667); docs/architecture/04-stdio-sandbox.md (L354, L376)

## Context

LiteMCP 需要决定支持哪些关系数据库，以及领域模型是否可以依赖某个数据库的专有能力（如 PostgreSQL 的 `JSONB`、partial unique index、GIN 索引，或 MySQL 的 `ON UPDATE`）。这直接影响迁移策略、CI 矩阵范围和领域代码的可移植性。

## Decision

1. PostgreSQL 14+ 与 MySQL 8.0+ 是**一级正式支持**数据库：两者都是发布阻断项，每次迁移和发布都必须进入 CI 矩阵且不得把 MySQL 标为 allow-failure（01-data-model.md L48-L52；08-implementation-plan.md L20, L341-L348）。
2. 领域层只使用一套逻辑类型（`ID`/`UTC_TS`/`JSON_DOC`/`CIPHERTEXT`/`LONG_TEXT`/`BOOL`/`ENUM_CODE`），由 `core/db/types.py` 的 SQLAlchemy `TypeDecorator` 和 `core/db/dialects/` 承担方言映射；业务代码禁止直接依赖 `JSONB`、PostgreSQL partial index、MySQL `ON UPDATE` 等单一数据库特性（01-data-model.md L36-L46）。不使用数据库原生 `ENUM` 类型，以便未来升级枚举取值。
3. 软删除后名称唯一性约束通过应用层 `uniqueness_scope` 模式实现，而不是依赖 PostgreSQL partial unique index，以保证 PostgreSQL/MySQL 语义一致；PostgreSQL 部署可以额外加只读性能索引，但不能改变约束语义（01-data-model.md L65-L75）。
4. 标签检索等场景不得为 PostgreSQL 单独用 GIN 索引后就声称"跨库一致"（03-service-crud.md L667）；生产回滚也不得假设 MySQL DDL 与 PostgreSQL 有相同的事务回滚能力（08-implementation-plan.md L133）。
5. **拒绝/暂缓的方案**：SQLite 明确**拒绝**作为生产数据库，也**拒绝**作为一级方言兼容性测试的替代——SQLite 测试通过不能代替 PostgreSQL/MySQL 兼容性测试，只用于验证轻量领域逻辑（01-data-model.md L48-L54）。MariaDB 10.6+、SQL Server 2019+ 被列为"二级适配目标"，在补齐驱动、Alembic 方言迁移和完整 CI 之前**不得**声明正式支持（01-data-model.md L48-L52）——即当前**未决定**是否/何时将其提升为一级支持。
6. 默认部署 profile 使用 PostgreSQL；MySQL 通过独立 service 和 `DATABASE_URL` 切换，同一环境只启动一个关系数据库（00-overview.md L141）。

## Consequences

- 正面：业务代码可移植，不会因迁移到另一数据库而重写领域逻辑；两个数据库都作为发布阻断项，避免"事实上只测过一个数据库"的隐性风险。
- 负面：工程成本更高——每个迁移、每个约束设计都必须在两个方言下验证（01-data-model.md L54 要求一级兼容性测试覆盖建库、全量迁移、升降级迁移、CRUD、并发切换、唯一约束、循环外键、事务回滚）；不能使用某个数据库的高级特性带来的性能/表达力便利（如 PostgreSQL 的 partial index、GIN）。
- **[待决，未来]**：MariaDB/SQL Server 何时评估为二级适配目标没有时间表，触发条件是"补齐驱动、Alembic 方言迁移和完整 CI"——这本身不是本 ADR 承诺的工作范围，需要单独立项时再决定。
- 重新评估条件：若领域模型演进中出现只有单一方言才能表达的必需约束，或 MariaDB/SQL Server 支持被产品明确排期，需要修订本 ADR。
