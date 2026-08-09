# ADR-0001: 使用 MCP 官方 SDK，锁定协议版本 2025-11-25，通过 version adapter 隔离未来破坏性升级

- Status: Accepted
- Date: 2026-08-09
- Source refs: docs/architecture/05-agent-gateway.md §1.3 (L35-L45); docs/architecture/00-overview.md (L20-L22); docs/architecture/08-implementation-plan.md §1.2 比较表 (L32); docs/architecture/04-stdio-sandbox.md (L20); docs/architecture/09-verification.md (L58)

## Context

LiteMCP 的 Agent 网关（HTTP 与 stdio 两个数据面）需要实现 Model Context Protocol。存在两种路径：手写一套近似 MCP 的私有协议，或采用官方 MCP SDK 并遵守其类型与生命周期语义。同时 MCP 协议本身仍在演进——`05-agent-gateway.md` 记录 2026-07-28 的协议草案已经移除了协议级 Session，属于破坏性变化，而 LiteMCP 当前的 Session/Task 数据模型（见 01-data-model.md）依赖协议级 Session 语义。

## Decision

1. 后端/网关/stdio 三处都使用 MCP 官方 SDK 的类型与 transport 实现，不复制一套私有 MCP Schema（00-overview.md L20；04-stdio-sandbox.md L20）。
2. 外部端点固定支持协议版本 `2025-11-25`：`initialize` 记录协商版本；后续请求必须携带与会话一致的 `MCP-Protocol-Version`，缺失时从已建立 Session 恢复，版本冲突或不支持时返回 HTTP 400（05-agent-gateway.md L43）。
3. `pyproject.toml` 与 lockfile 必须固定一组已通过契约测试的 SDK 版本，生产镜像不得浮动升级（05-agent-gateway.md L43；09-verification.md L58 禁止 CI/Inspector 使用 `latest` 标签或浮动运行时安装）。
4. 首期只暴露 MCP `tools` 能力，不伪造 resources/prompts/sampling/elicitation；不兼容已废弃的 2024-11-05 HTTP+SSE 双端点；MCP Tasks 是实验能力，首期默认关闭（05-agent-gateway.md L35-L39）。
5. **拒绝的方案**：手写近似 MCP 协议实现（08-implementation-plan.md L32 比较表明确拒绝，理由是官方 SDK 有真实 client 测试且类型统一）；在现有 2025-11-25 代码路径上用条件分支直接适配未来协议变化（05-agent-gateway.md L45 明确要求走 version adapter，而不是条件分支）。

## Consequences

- 正面：协议正确性由官方 SDK 保证，减少自研协议 bug 面；版本锁定使生产行为可复现，避免 SDK 静默升级引入未契约测试过的行为变化。
- 负面：SDK 版本升级是有意的、需要走契约测试的工程动作，不能"顺手"跟随上游最新版本；首期不支持 resources/prompts/sampling/elicitation/Tasks，如果这些能力被产品提前需要，需要新的能力开放决策（见 08-implementation-plan.md §2.1 能力开放顺序表）。
- **[建议，暂缓]**：为应对 2026-07-28 起协议移除 Session 的破坏性变化，未来升级协议版本时必须把 transport/session 实现放在版本 adapter 后面，通过新增契约测试引入新版本，而不是修改既有 2025-11-25 语义；触发条件是"确定要支持新协议版本"时，且升级前必须同步修改 01-data-model.md 的 Session/Task 模型与 09-verification.md 的验证矩阵（05-agent-gateway.md L45）。在该 adapter 设计完成前，不得开始协议版本升级工作。
- 重新评估条件：MCP 官方 SDK 发布不兼容当前 pinned 版本的破坏性变更，或产品需要 resources/prompts/sampling/elicitation/Tasks 中任一能力时，需要新的 ADR 或修订本 ADR。
