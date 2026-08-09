# ADR-0003: Outbox/Worker 采用至少一次投递 + 幂等可重入 handler + generation CAS，暂不做通用 Idempotency-Key

- Status: Accepted
- Date: 2026-08-09
- Source refs: docs/architecture/01-data-model.md §5.14 (L459); docs/architecture/03-service-crud.md §5.1, §6.2, §6.3 (L343-L371, L446, L452-L459); docs/architecture/00-overview.md (L150, L211-L212); docs/architecture/09-verification.md (L135, L152, L189, L192, L266); docs/architecture/07-observability.md (L171-L172, L332, L377, L412, L450)

## Context

服务创建/发布等操作需要在数据库事务提交后触发异步 worker（build/sync）。消息队列/outbox 与数据库事务提交之间不存在天然的"恰好一次"保证——需要决定投递语义（至少一次 vs 恰好一次）、handler 是否需要处理重复消息，以及是否要为 HTTP 客户端提供通用的 `Idempotency-Key` 语义。

## Decision

1. 审计/outbox 写入必须与业务事务同库同事务完成（transactional outbox），或由认证/网关安全审计通道单独写入失败尝试；普通运行日志不得替代审计表（01-data-model.md L459）。创建事务必须原子完成 service、creator editor、初始 revision 和成功审计/outbox，任一步失败则全部回滚；队列/outbox 无法与数据库提交建立可靠交付时事务失败并返回 503，**禁止**创建永远不会被 worker 看见的 pending service（03-service-crud.md L365, L371）。
2. Worker/outbox 按**至少一次投递**设计，不承诺恰好一次。Handler 必须可重入：先读取 run 状态和 `requested_generation`；终态重复消息直接确认；构建/网络完成后用 CAS 提交；消息确认发生在数据库终态提交之后。超时、进程崩溃或重复投递不得产生半发布状态（03-service-crud.md L459）。
3. 用两种独立的并发令牌：`row_version` 保护用户管理写，`generation` 保护异步 worker 的发布/回退 CAS；两者不能互相替代（03-service-crud.md L446）。Worker 任务创建必须按 `(service_id, requested_generation, operation_kind)` 或等价约束/锁去重，避免同一配置产生多个可发布候选；重复执行仍靠 generation CAS 保证安全（03-service-crud.md L453）。
4. **既定决策：MVP 不声称支持通用 `Idempotency-Key` 强幂等**（03-service-crud.md L452 明确标注为既定决策，而非默认省略）——理由是仅把 key 放 Redis 或内存而业务写在数据库，不能宣称 exactly-once。
5. **拒绝的替代方案**：为 HTTP 客户端实现通用 `Idempotency-Key` 强幂等系统属于 MVP 范围外的方案，被明确标注为 **[建议，暂缓]**：若真实客户端存在大量超时重试问题，再引入持久化 `idempotency_record`（03-service-crud.md L455）。

## Consequences

- 正面：at-least-once + 可重入 handler + CAS 的组合不依赖消息队列提供强保证，容错性来自数据库终态和 generation 令牌，简化了对底层队列实现的信任要求；outbox 崩溃重放不会产生重复副作用（09-verification.md L192 有专门的崩溃重放测试要求）。
- 负面：所有 worker handler 都必须自行实现"先读终态再决定是否跳过"的重入逻辑，不能假设消息只会被处理一次；这对每个新增的异步任务类型都是强制的工程纪律，遗漏会导致重复副作用。
- 可观测性联动：需要专门指标 `litemcp_audit_outbox_pending`、`litemcp_audit_outbox_oldest_age_seconds` 和审计投递新鲜度 SLO（99.9% ≤ 60s），并配置 `LiteMCPAuditDeliveryLag` 告警（07-observability.md L171-L172, L332, L377, L412, L450）。
- **[建议，暂缓]**：持久化 `idempotency_record` 用于 HTTP 层强幂等——触发条件是"观测到真实客户端存在大量超时重试"；在该条件出现前不得实现，以避免过早引入未验证需求的复杂度。
- 重新评估条件：观测到客户端超时重试导致的重复副作用问题，或消息队列/outbox 组件更换时，需要修订本 ADR。
