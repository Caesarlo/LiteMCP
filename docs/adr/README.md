# 架构决策记录（ADR）

本目录记录 LiteMCP 的架构决策，采用 [Michael Nygard 风格](https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions)的轻量模板（见 [template.md](template.md)）：Status / Context / Decision / Consequences 四段，一次决策一个文件，编号递增、不修改历史文件的编号或既有内容——决策变化时新增一条并在新旧文件之间建立 Superseded 关系，而不是原地重写。

## 何时写一条新 ADR

- 影响多个模块或后续里程碑、一旦改变代价较高的技术选型（协议版本、数据存储策略、对外契约、安全信任边界等）。
- `docs/architecture/` 中的架构文档给出了"决定"但分散在多处，需要一个单一、可追溯的记录点。
- `08-implementation-plan.md` 或其他文档中明确用 `[建议]`/`[后续]` 标记为暂缓的方案被重新评估、转正或否决时。

不需要为每一次代码实现细节写 ADR；只有会改变后续决策空间的选择才值得记录。日常实现选择走 `progress.md` 的 checkpoint，不必单独建 ADR。

## 命名与结构

- 文件名：`NNNN-kebab-case-标题.md`，四位数字编号从 `0001` 开始递增，不回收已废弃条目的编号。
- 每篇 ADR 的 `Source refs` 必须指向 `docs/architecture/*.md` 的具体文件与章节/行号，说明这条决策的依据来自哪里，而不是凭空写出的新决定。
- `Consequences` 必须区分"已承诺（[既定]/[决定]）"和"暂缓（[建议]/[后续]）"两类后果，并写明暂缓部分的触发条件，避免暂缓项被误当作已完成。

## 索引

| ADR | 标题 | Status |
|---|---|---|
| [0001](0001-mcp-sdk-version-pinning.md) | MCP 官方 SDK，锁定协议版本 2025-11-25 | Accepted |
| [0002](0002-db-dialect-strategy.md) | PostgreSQL 14+ 与 MySQL 8.0+ 双一级支持 | Accepted |
| [0003](0003-outbox-worker-reliability.md) | Outbox/Worker 至少一次投递 + 幂等可重入 handler | Accepted |
| [0004](0004-feature-flag-registry.md) | 自建 typed feature flag registry，暂缓 OpenFeature | Accepted |
| [0005](0005-object-storage-registry-interface.md) | StorageBackend + 不可变 OCI 镜像按 digest 运行 | Accepted |
| [0006](0006-reverse-proxy-trusted-proxy.md) | 只信任显式配置的反向代理（具体拓扑留待 M2） | Accepted（部分未决，见文内） |

以上 6 篇覆盖 [08-implementation-plan.md](../architecture/08-implementation-plan.md) M0 交付要求中列出的 ADR 议题（MCP SDK/version、DB 方言策略、outbox/worker 机制、feature flag registry、对象存储/Registry 接口、生产反向代理与 trusted proxy）。它们的 Decision 内容均提炼自现有架构文档中已经写明的立场，而不是本次新做出的决定；ADR-0006 是唯一一篇明确标注"具体拓扑尚未决定"的条目，见其 Consequences 一节。
