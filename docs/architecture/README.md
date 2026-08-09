# LiteMCP 架构设计文档索引

本目录按功能模块拆分自原单文件 `docs/ARCHITECTURE_PLAN.md`，便于分模块评审和后续维护。阅读顺序建议按下表从上到下。

| 文档 | 内容 |
|---|---|
| [00-overview.md](00-overview.md) | Context、技术栈、部署形态、目录结构、Makefile |
| [01-data-model.md](01-data-model.md) | 跨数据库领域模型（配置版本、构建、原子工具集、MCP Schema、权限、API Key、审计与加密） |
| [02-admin-auth.md](02-admin-auth.md) | 管理后台鉴权（密码、JWT、refresh 会话、浏览器安全、权限与审计） |
| [03-service-crud.md](03-service-crud.md) | 三类服务 CRUD（管理侧） |
| [04-stdio-sandbox.md](04-stdio-sandbox.md) | STDIO 沙箱（构建/运行容器、安全边界、镜像管理、并发与背压） |
| [05-agent-gateway.md](05-agent-gateway.md) | Agent 侧网关（鉴权、限流、connector 分发） |
| [06-frontend.md](06-frontend.md) | 前端设计 |
| [07-observability.md](07-observability.md) | 可观测性（metrics/日志） |
| [08-implementation-plan.md](08-implementation-plan.md) | 实施步骤（纵向切片） |
| [09-verification.md](09-verification.md) | 验证方式 |
| [10-tdd-execution-plan.md](10-tdd-execution-plan.md) | TDD 执行计划：编码方案与 UI/UX 流程按里程碑拆分 |

**评审记录**：本计划已经过五轮针对性评审（数据模型 / 鉴权限流 / STDIO 沙箱 / 整体架构 / 镜像资源与限流细化）。「必须改」「建议改」的结论已吸收进对应模块文档；「可选优化」项在各文档中以「后续可选」标注，本轮不做但明确记录。**构建产物如何从构建容器搬运到对象存储再到运行容器**这一项复杂度较高，明确留作**单独一轮评审**（见 [04-stdio-sandbox.md](04-stdio-sandbox.md) 末尾标注）。
