# ADR-0004: 首期自建小型 typed feature flag registry，安全能力默认 false；暂缓接入 OpenFeature

- Status: Accepted
- Date: 2026-08-09
- Source refs: docs/architecture/08-implementation-plan.md §1.2 比较表 (L11, L21, L35); docs/architecture/08-implementation-plan.md (L321, L408, L453); docs/architecture/05-agent-gateway.md（`gateway.enabled` 用法，经 09-verification.md L291 印证）; docs/architecture/04-stdio-sandbox.md (L29)

## Context

LiteMCP 需要按环境和风险等级逐步开放能力（管理登录、`http_api`、`agent_auth_mode=none`、`mcp_http`、`stdio` 上传/构建/运行、OAuth 2.1、MCP Tasks、stdio 多副本等，见 08-implementation-plan.md §2.1 能力开放顺序表）。业界方案 OpenFeature 提供了 flag 评估与供应商控制面解耦的标准 API，但需要一个可用的远程控制面 provider。这是六个 M0 ADR 主题中，文档里只有一处集中陈述（08-implementation-plan.md L35 单行），没有独立小节展开的一条——本 ADR 基本是把这一行决策及其分散引用聚合、正式化。

## Decision

1. 首期实现一个小型的、类型化的 in-house feature flag registry；安全相关能力默认值一律为 `false`（关闭）（08-implementation-plan.md L35）。
2. 未声明支持的能力必须由 feature flag/capability fail-closed，不得对外暴露（08-implementation-plan.md L11 标记legend，适用于全文 [后续] 项）。
3. Feature flag 是"可回滚单元"的构成要素之一：代码、迁移、配置、feature flag、镜像 digest 和验证证据均可追踪；业务回滚不依赖不可逆的数据库 downgrade（08-implementation-plan.md L21）。回滚流程第一步就是关闭产生新状态的 feature flag（新建、同步、构建），而不是先删数据（08-implementation-plan.md L321）。
4. 发布候选门禁要求"feature flag 默认/回退行为演练完成"（08-implementation-plan.md L408）。
5. **拒绝/暂缓的方案**：直接首期接入 OpenFeature 远程控制面被**拒绝**，理由是 provider 未就绪时也需要有明确默认值，先自建小 registry 可以立即满足这个要求，不引入对外部控制面组件的运行时依赖。OpenFeature provider 接入被明确标记为 **[后续]**：需要远程控制面（例如运营需要不重新部署即可切换 flag）时再接入（08-implementation-plan.md L35）。

## Consequences

- 正面：flag 求值不依赖任何外部服务的可用性，避免"控制面故障导致核心功能不可用或误开启"的风险；类型化 registry 可以在编译期/启动期捕获拼写错误等低级问题。
- 负面：不具备 OpenFeature 生态的现成能力（如远程动态下发、A/B 分桶、供应商 UI 面板）；能力开放顺序表中列出的每个能力（gateway.enabled 等）都需要手工定义对应的 flag 及其默认值和 fail-closed 语义，工程纪律要求高。
- **[后续，暂缓]**：接入 OpenFeature provider——触发条件是"需要远程控制面"（例如需要不经过部署流程即可切换生产 flag）。在该需求明确出现前不实现，避免为不存在的运营场景预先引入依赖。
- 重新评估条件：产品需要非工程人员能够在不发布代码的情况下切换生产环境的某个 flag，或需要跨多环境集中管理 flag 状态时，需要修订本 ADR 并评估 OpenFeature provider 接入。
