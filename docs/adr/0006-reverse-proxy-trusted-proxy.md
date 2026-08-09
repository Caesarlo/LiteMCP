# ADR-0006: 后端只信任显式配置的反向代理；转发头必须被代理覆盖而非透传（具体代理拓扑留待 M2 入口决定）

- Status: Accepted（信任边界原则）— 具体反向代理拓扑/TLS 终止方式仍未决定，见「Consequences」
- Date: 2026-08-09
- Source refs: docs/architecture/02-admin-auth.md §3 (L43-L50, L413); docs/architecture/05-agent-gateway.md (L85, L133, L453); docs/architecture/08-implementation-plan.md (L101, L140); docs/architecture/07-observability.md (L62)

## Context

管理前端/API 与 Agent 网关都部署在反向代理之后，代理会转发 `X-Forwarded-For`、`X-Forwarded-Proto` 和 Host 相关头部。如果后端无条件信任这些客户端可控的头部，速率限制、审计 IP、CORS/Origin 校验等一切依赖来源 IP/协议判断的逻辑都可能被伪造绕过。需要确定信任边界的默认姿态，以及生产环境启动时如何对错误配置 fail-closed。

## Decision

1. 后端**只信任明确配置的反向代理**；代理必须**覆盖**而不是透传客户端伪造的 `X-Forwarded-For`、`X-Forwarded-Proto` 和 Host 相关 Header（02-admin-auth.md L48）。Agent 网关侧重申同一原则：TLS 是生产前提，后端只信任来自显式 trusted proxy 网段的 forwarded IP/scheme，禁止任意客户端伪造 `X-Forwarded-For`（05-agent-gateway.md L453）。
2. 除本机开发环境外，管理前端和 API 全程只允许 HTTPS，生产启用 HSTS；推荐前端和 API 使用同一站点、同一 Origin 由反向代理统一暴露，确需跨 Origin 时只能配置精确 Origin allowlist；CORS 禁止 `*` 与 credentials 组合（02-admin-auth.md L45-L47）。
3. M0 退出标准要求：生产配置若使用示例/短密钥、宽泛 Origin、**错误的 trusted proxy 配置**或 debug 模式，启动必须**拒绝**，而不是带着不安全配置继续运行（08-implementation-plan.md L101）。
4. `/metrics`、健康检查和运维接口不复用管理 JWT，需按可观测性文档通过内网或运维访问控制隔离，且不能公网匿名可达（02-admin-auth.md L49；07-observability.md L62）。
5. 针对 Agent 网关的流式响应，反向代理必须关闭 SSE buffering，读超时大于应用最大调用时间，并传递客户端断线信号；禁止代理缓存 MCP 响应，返回 `Cache-Control: no-store`、`X-Content-Type-Options: nosniff`（05-agent-gateway.md L85, L133）。
6. 第一版密码认证的前提是部署在内网、VPN 或有反向代理访问控制的环境；若管理端直接暴露到互联网，生产基线必须至少启用一种额外强认证控制（02-admin-auth.md L413）——这是信任边界原则的一个直接推论，而非独立决定。

## Consequences

- 正面：默认拒绝未配置来源的转发头，从根本上防止 IP 伪造类的限流/审计绕过；启动期 fail-closed 检查把配置错误变成部署时的硬失败，而不是生产环境里悄悄失效的安全控制。
- 负面：每个部署环境都必须显式配置 trusted proxy（网段/CIDR），配置遗漏或写错会直接导致启动失败，这是有意为之的行为，但需要在部署文档中明确说明排障方式。
- **明确未决事项（不在本 ADR 中臆造决定）**：具体的反向代理产品/拓扑、管理 Origin 的最终取值和 Cookie 安全属性、TLS 终止方式，`08-implementation-plan.md` 把这些列为 **M2（管理鉴权与后台壳）里程碑的入口标准**（L140："反向代理、管理 Origin、Cookie 安全属性和 TLS 终止方式已确定"），说明在 M0/M1 阶段这些具体选择尚未做出。本 ADR 只记录已经文档化的信任边界**原则**（只信任显式配置的代理、必须覆盖转发头、fail-closed），不预先替 M2 做拓扑选型；进入 M2 前必须先做出该拓扑决定，可另立 ADR 或修订本 ADR 补充 Decision 部分。
- 重新评估条件：M2 入口标准中的具体反向代理拓扑/TLS 终止方式被确定时，应更新本 ADR（或新增一条 ADR 并在此处建立 Superseded/补充关系），把"未决事项"转为已记录的具体决定。
