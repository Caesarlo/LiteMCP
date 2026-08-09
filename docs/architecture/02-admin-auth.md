# 02 · 管理后台鉴权

[← 返回索引](README.md)

本文定义 LiteMCP 管理后台的身份认证、浏览器会话、用户状态、全局角色和 service 级授权契约。管理后台与 Agent 调用端是两套独立安全边界：本篇只适用于 `/api/v1/auth/*` 和 `/api/v1/admin/*`；Agent 侧的 API Key、无鉴权模式和后续 OAuth 见 [05-agent-gateway.md](05-agent-gateway.md)。

本文中的“必须”“禁止”“默认”均属于第一版实现和验收要求，不是示例建议。部署可以收紧参数，但不能关闭签名校验、refresh 轮换、用户状态检查、默认拒绝或审计等安全不变量。

## 1. 目标、范围与威胁模型

### 1.1 设计目标

- 浏览器长期凭据不暴露给 JavaScript，不进入 `localStorage`、`sessionStorage`、URL、日志或审计事件。
- access token 泄漏后的可利用窗口有明确上限；refresh token 被重复使用时能够检测并终止整个会话。
- 用户禁用、全局角色变化和 service 权限变化在下一次管理请求立即生效，不依赖旧 JWT 自然过期。
- 所有管理 API 默认拒绝，认证和对象级授权不能只依赖前端隐藏按钮。
- 登录、刷新、登出、锁定、权限拒绝和敏感管理操作均可审计，但不记录密码、Token、Cookie 或 Authorization Header。
- Redis、数据库、反向代理或时钟异常时有确定的 fail-closed 行为。

### 1.2 保护资产

- 管理员和普通用户账号。
- 服务配置、上游秘密、构建产物和工具定义。
- service 权限、Agent API Key 和鉴权模式。
- access JWT 签名密钥、refresh session、密码哈希和审计证据。

### 1.3 主要威胁

本设计至少覆盖凭据填充和密码喷洒、用户枚举、XSS 窃取 Token、CSRF、JWT 算法混淆、Token 重放、会话固定、并发 refresh、IDOR/对象级越权、旧权限继续生效、Redis 故障和日志泄密。第一版不实现设备指纹强绑定、行为风控或钓鱼抵抗型 MFA；公网暴露时的额外要求见第 15 节。

## 2. 方案选择

第一版采用以下混合方案：

1. 密码在服务端使用 Argon2id 验证。
2. 登录成功后返回短时效 access JWT，同时通过 HttpOnly Cookie 写入 opaque refresh token。
3. access JWT 仅保存在浏览器内存，通过 `Authorization: Bearer <token>` 调用管理 API。
4. refresh token 的会话状态和当前 token 摘要保存在 Redis；每次刷新都单次轮换，旧 token 再出现即判定为重放。
5. JWT 只证明短期身份，不作为权限真源。后端每次请求都读取用户当前状态和全局角色；涉及 service 时再读取 `mcp_service_permission`。

该方案保留短期 JWT 对 API 调用和未来服务拆分的兼容性，同时用服务端 refresh session 提供登出、踢出、空闲超时和重放检测。第一版不维护 access token 逐 `jti` 黑名单；已签发 access token 在普通登出或 refresh 会话吊销后最多继续有效到自身 `exp`，默认不超过 15 分钟。用户禁用、密码修改和角色/权限变化不受该窗口影响，按第 11 节立即生效。

## 3. 部署和传输前提

- 除本机开发环境外，管理前端和 API 全程只允许 HTTPS；生产启用 HSTS。
- 推荐前端和 API 使用同一站点、同一 Origin，由反向代理统一暴露。确需跨 Origin 时只能配置精确 Origin allowlist。
- CORS 禁止使用 `*` 与 credentials 组合；只允许后台实际使用的方法和 Header。
- 后端只信任明确配置的反向代理。代理必须覆盖而不是透传客户端伪造的 `X-Forwarded-For`、`X-Forwarded-Proto` 和 Host 相关 Header。
- `/metrics`、健康检查和运维接口不复用管理 JWT；按 [07-observability.md](07-observability.md) 通过内网或运维访问控制隔离。
- 认证响应统一带 `Cache-Control: no-store`；密码、Token、Cookie 和用户私有响应不得被共享缓存。

## 4. 用户初始化和生命周期

### 4.1 首个管理员

第一版不提供公开注册接口。首个管理员通过 CLI `litemcp admin create` 创建，命令直接连接数据库或调用仅本机可访问的初始化逻辑：

- 数据库事务内锁定初始化状态，确认不存在任何用户后创建首个 `role=admin,status=active` 用户。
- 两个并发初始化只能有一个成功。
- 密码从交互式终端读取或通过受保护的 secret input 提供，禁止出现在命令行参数、shell history 和日志中。
- 初始化完成后不存在可重复使用的 bootstrap HTTP 接口或默认账号。

[09-verification.md](09-verification.md) 中的“注册管理员”特指该一次性初始化流程，不是匿名自助注册。

### 4.2 后续用户

- 只有 admin 可以创建用户、禁用/启用用户和修改全局角色。
- 不允许禁用或降权最后一个 active admin。
- 用户不物理删除，以保留审计主体；离职和撤权使用 `status=disabled`。
- 第一版不实现邮件找回。忘记密码由 admin 发起重置，或由受控 CLI 重置；重置后必须吊销该用户全部 refresh session。
- 用户名按 [01-data-model.md](01-data-model.md) 的 NFKC + trim + casefold 规则生成 `username_normalized`，登录、唯一性和限速均使用规范化值。

## 5. 密码策略和存储

### 5.1 新密码规则

- 单因素密码最少 15 个 Unicode code point，最多 128 个 Unicode code point；请求体仍设置独立字节数上限，防止超长输入造成哈希 DoS。
- 允许空格、Unicode、粘贴、浏览器自动填充和密码管理器，不要求大小写、数字或特殊字符组合。
- 创建和修改密码时，对完整密码执行常见、预期和已泄漏密码 blocklist 检查；至少包含用户名、`LiteMCP` 及常见变体。
- 不做周期性强制轮换。只有用户主动修改、管理员重置或存在泄漏证据时才要求更换。
- 密码永远不写入日志、trace、审计 metadata 或错误详情。

### 5.2 哈希算法

- 新密码统一使用 Argon2id，保存标准 PHC 字符串，使算法、salt 和参数可自描述。
- 基线参数不低于 `m=19456 KiB,t=2,p=1`，最终参数由目标部署环境压测确定；单次校验目标应兼顾抗破解能力和登录并发，不能机械提高到形成 CPU/内存 DoS。
- 每个密码使用哈希库生成的独立随机 salt。可选 pepper 必须来自 Secret Manager，并与数据库、JWT 密钥分离。
- 登录成功时执行 `needs_rehash`；参数升级后在同一受控流程重新哈希。
- 若迁移已有 bcrypt 哈希，验证成功后立即升级为 Argon2id。bcrypt 只作为旧数据兼容，不作为新密码默认算法。

### 5.3 修改和重置

- 用户自行修改密码必须提交当前密码，并受登录限速保护。
- 修改成功后更新 `password_changed_at`，吊销该用户全部 refresh session，然后创建一个新的当前会话或要求重新登录；两种行为必须固定选择，第一版选择“要求重新登录”。
- 管理员重置他人密码属于敏感操作，需要第 14 节的 step-up 认证，并吊销目标用户全部 refresh session。

## 6. 登录和失败锁定

### 6.1 登录流程

`POST /api/v1/auth/login` 接受 `username/password`，固定按以下顺序处理：

1. 校验请求 Content-Type、请求体长度、Origin/Fetch Metadata 和登录限速。
2. 规范化 username 并查询用户；用户不存在时仍执行一次固定的 dummy Argon2id 校验，降低响应时序差异。
3. 在数据库事务内读取并锁定用户行，处理已过期锁定和失败观察窗口。
4. 仅当 `status=active` 时校验密码；`disabled` 和仍在锁定期内的用户走统一失败响应。
5. 密码失败时原子增加失败计数，必要时进入 locked 状态并写安全审计。
6. 密码成功时把失败计数和窗口清零，更新 `last_login_at`，创建 Redis refresh session，签发 access JWT。
7. 只有数据库和 Redis 均成功后才返回登录成功；任一持久化失败均不签发可用 refresh token。

登录成功返回 access token、过期秒数和最小用户摘要；refresh token 只通过 Cookie 返回。响应示例：

```json
{
  "access_token": "<jwt>",
  "token_type": "Bearer",
  "expires_in": 900,
  "user": {
    "id": "<uuid>",
    "username": "alice",
    "role": "user"
  }
}
```

### 6.2 统一失败响应

账号不存在、密码错误、`disabled` 和 `locked` 对未认证调用者统一返回：

```json
{
  "error": {
    "code": "INVALID_CREDENTIALS",
    "message": "用户名或密码错误，或账号当前不可登录",
    "request_id": "<request-id>"
  }
}
```

- HTTP 状态统一为 401；不得返回账号是否存在、具体状态、失败次数或 `locked_until`。
- 来源或账号维度触发请求节流时返回 429 `AUTH_RATE_LIMITED` 和 `Retry-After`，但响应仍不能证明账号存在。
- 内部 `audit_event.reason_code` 可以使用 `USER_NOT_FOUND`、`BAD_PASSWORD`、`ACCOUNT_DISABLED`、`ACCOUNT_LOCKED` 等具体原因。
- 前端只展示统一提示；账号状态由已认证 admin 在用户管理页查看。

### 6.3 限速和锁定状态机

登录防护同时使用：

- 来源维度 Redis 限速：按可信客户端 IP 的 HMAC/规范值限制扫描和暴力尝试。
- 账号维度失败窗口：按 `username_normalized`/user ID 统计分布式尝试。
- 数据库账号锁定：达到阈值后写入 `status=locked,locked_until`，作为跨实例强一致状态。

默认参数为 15 分钟观察窗口内失败 5 次，锁定 15 分钟，均可配置。`user` 表应增加 `failed_login_window_started_at`；只使用永久累计的 `failed_login_count` 会导致跨数月的零散失败也触发锁定。

状态规则：

- `disabled` 优先级最高，不能因锁定到期自动变回 active。
- `locked` 且 `locked_until > now`：拒绝登录。
- `locked` 且锁定已到期：在处理本次密码前原子改回 active，并重置失败计数和观察窗口。
- active 用户的首次失败创建观察窗口；窗口到期后出现的新失败从 1 重新开始。
- 成功登录清零失败计数和观察窗口。
- admin 手动解锁必须写审计，但不能启用 disabled 用户。
- 所有登录失败、锁定、解锁和成功登录均写安全审计；对高频失败事件可异步批量落库，但不能只写普通日志。

## 7. Access JWT

### 7.1 生命周期与存储

- 默认有效期 15 分钟，可在 10–30 分钟范围配置；生产不允许配置为无限期。
- access token 只保存在 React 运行时内存，不写 `localStorage`、`sessionStorage`、IndexedDB、Cookie 或 URL。
- 页面加载后如内存中无 access token，由前端在跨 Tab 锁保护下调用 refresh。
- API client 只向受信 API Origin 添加 Authorization Header，禁止把 Token 发送到对象存储、上游 MCP 服务或外部 URL。

### 7.2 Header 和 Claims

第一版固定一种受支持算法。单体部署可以使用至少 256 bit 随机密钥的 HS256；若验证职责拆到多个服务，优先切换 EdDSA/ES256，避免所有验证方都持有可签发的对称密钥。无论选择哪种算法，验证器都必须由服务端配置固定 allowlist，不能根据 Token Header 自由选择。

JWT Header 至少包含：

```json
{
  "alg": "HS256",
  "kid": "admin-jwt-2026-01",
  "typ": "at+jwt"
}
```

Claims 至少包含：

| Claim | 语义 |
|---|---|
| `iss` | 固定 LiteMCP 管理认证 issuer |
| `aud` | 固定管理 API audience，不能用于 Agent 网关 |
| `sub` | `user.id` 的规范 UUID 字符串 |
| `sid` | 本次登录的 refresh session ID，用于审计关联 |
| `jti` | access token 唯一 ID |
| `iat` | 签发时间 |
| `nbf` | 最早可用时间 |
| `exp` | 绝对过期时间 |
| `token_type` | 固定 `access` |

JWT 不保存 service 权限、密码状态、上游秘密或个人敏感信息。全局 `role` 即使为前端展示而携带也不能用于后端授权，后端始终使用数据库当前值。

### 7.3 验证规则

验证器必须：

- 拒绝 `alg=none`、不在 allowlist 的算法、未知 `kid` 和错误 `typ/token_type`。
- 验证签名、`iss`、`aud`、`sub` 格式、`iat/nbf/exp`，时钟偏差最多允许 30 秒。
- 拒绝重复或类型错误的安全 Claim、异常大的 Token 和无法解析的 Token。
- 根据 `sub` 查询当前用户，要求存在且 `status=active`。
- 若 `iat < password_changed_at`，拒绝为 `TOKEN_STALE`。
- 使用数据库当前 `role` 构造认证上下文，不信任 JWT 中的角色。

签名密钥来自受保护配置，支持通过 `kid` 并行保留当前签发 key 和尚未过完最长 access TTL 的旧验证 key。JWT 密钥不得与 Fernet、CSRF、API Key pepper 或 refresh 摘要密钥复用。

## 8. Refresh Session 与轮换

### 8.1 Token 格式

refresh token 使用 opaque 格式：

```text
<session_id>.<random_secret>
```

- `session_id` 至少 128 bit CSPRNG，用作 Redis 单行定位符。
- `random_secret` 至少 256 bit CSPRNG。
- Redis 不保存完整 Token 或 secret 明文，只保存 `SHA-256(random_secret)`；如使用 HMAC，pepper 必须与其他用途分离。

### 8.2 Cookie

生产 Cookie 固定为：

```text
Set-Cookie: __Secure-litemcp_rt=<token>; Path=/api/v1/auth; HttpOnly; Secure; SameSite=Strict; Max-Age=<remaining-absolute-ttl>
```

- 禁止设置 `Domain`，避免子域共享。
- 本机 HTTP 开发可以通过独立的 development 配置关闭 `Secure`，该配置不得用于生产。
- refresh、logout 和密码修改响应必须 `Cache-Control: no-store`。
- 清除 Cookie 时必须使用完全相同的 name/path/secure/samesite 属性，并设置 `Max-Age=0`。

### 8.3 Redis 模型

Redis key 示例：`litemcp:<environment>:admin_session:<session_id>`，TTL 不超过 absolute expiry。value 至少保存：

- `user_id`
- `current_secret_hash`
- `created_at`
- `last_refreshed_at`
- `idle_expires_at`
- `absolute_expires_at`
- `user_agent_hash`（只用于审计/异常提示，不作为硬绑定）
- `source_ip_hash`（可选，不保存原始 IP）

另维护 `litemcp:<environment>:user_sessions:<user_id>` 集合，以支持禁用、修改密码和“退出全部设备”；集合本身也必须有 TTL/清理机制。

默认 idle timeout 为 8 小时、absolute timeout 为 7 天。refresh 只延长 idle expiry，永远不能超过 absolute expiry。

### 8.4 原子轮换与重放检测

`POST /api/v1/auth/refresh` 通过 Redis Lua 或等价原子事务执行：

1. 解析 session ID 和 secret，校验格式及长度。
2. 读取 session；不存在、idle/absolute 过期均拒绝并清 Cookie。
3. 常量时间比较请求 secret 摘要与 `current_secret_hash`。
4. 不匹配表示旧 token 重放或伪造：删除整个 session，写 `auth.refresh_reuse_detected` 审计和告警，不再签发 Token。
5. 匹配则生成新 secret，把摘要、last refreshed 和 idle expiry 原子替换。
6. 查询当前用户；用户必须 active，且会话创建时间不能早于 `password_changed_at`。
7. 返回新 access JWT，并用 Set-Cookie 覆盖 refresh token。

第一版不设置“旧 refresh token 宽限窗口”，避免攻击者利用宽限重复换取 Token。前端必须使用 Web Locks API 或 BroadcastChannel 实现跨 Tab refresh single-flight；同一时刻只允许一个 Tab 刷新，其余 Tab等待结果。Axios 收到多个 401 时也只能启动一次刷新。

## 9. CSRF、CORS、XSS 和浏览器防护

### 9.1 CSRF

access token 使用 Authorization Header，不由浏览器自动附带，普通管理 API 不依赖 Cookie 认证。refresh、logout 等接口会自动携带 refresh Cookie，必须额外防护：

- 默认只允许同 Origin 请求。
- 校验 `Origin`；缺失时按明确策略校验 `Referer`，值不匹配则拒绝。
- 对现代浏览器校验 `Sec-Fetch-Site`，拒绝 `cross-site` 状态变更请求。
- 前端为登录、refresh、logout 使用自定义 `X-LiteMCP-Request: 1` Header；CORS 不允许未知 Origin发送该 Header。
- 所有状态变更只使用 POST/PUT/PATCH/DELETE，禁止 GET 产生副作用。

若未来必须支持跨站部署或 access token 改用 Cookie，必须引入与 session 绑定的 synchronizer CSRF token 或签名 double-submit cookie；不能只依赖 SameSite。

### 9.2 XSS 与安全 Header

HttpOnly 只能防止 JavaScript 读取 refresh token，不能阻止 XSS 以用户身份发请求，因此前端和反向代理还必须：

- 配置严格 Content Security Policy，生产禁止任意 inline script 和 `unsafe-eval`。
- 对用户可控 HTML、Markdown、图标和 MCP metadata 做安全渲染/清洗。
- 设置 `X-Content-Type-Options: nosniff`、合理的 `frame-ancestors`/`X-Frame-Options` 和 `Referrer-Policy`。
- 登出时清内存 access token；可按部署兼容性使用 `Clear-Site-Data` 清理缓存和存储，但不能误删同 Origin 的其他应用数据。

## 10. 认证依赖执行顺序

所有 `/api/v1/admin/*` 路由在 router 层默认挂载认证依赖，禁止依赖开发者逐接口手工添加。只有显式列出的 `/api/v1/auth/login`、健康检查和内部运维端点例外。

`get_current_user` 固定执行：

1. 读取 Bearer token，缺失返回 401。
2. 完整验证 JWT Header、签名和 Claims。
3. 查询 `user`，检查 `status=active` 和 `password_changed_at`。
4. 从数据库读取当前全局角色，构造 `CurrentUser`。
5. 为审计上下文附加 request ID、user ID、JWT `sid/jti` 的不可逆摘要；不得记录 Token 本身。

数据库不可用时认证 fail-closed，返回 503，不允许退化为只信任 JWT 中的旧角色或状态。

## 11. 失效语义

| 事件 | Refresh session | 已签发 access token | 权限效果 |
|---|---|---|---|
| 普通登出 | 删除当前 session | 浏览器立即丢弃；泄漏副本最多存活到 `exp` | 不变 |
| 退出全部设备 | 删除用户全部 session | 最多存活到 `exp` | 不变 |
| refresh 重放 | 删除当前 token family | 最多存活到 `exp` | 不变，并告警 |
| 用户 disabled | 删除全部 session | 下一请求因 DB 状态立即拒绝 | 立即失效 |
| 修改/重置密码 | 更新 `password_changed_at`，删除全部 session | 下一请求因 `iat` 过旧立即拒绝 | 立即失效 |
| 全局角色变化 | 建议删除全部 session | 后端下一请求读取新角色 | 立即生效 |
| service 权限变化 | 无需删除 session | access token 不承载 service 权限 | 下一请求立即生效 |
| JWT 签名密钥紧急泄漏 | 轮换签名 key；必要时删除全部 session | 移除泄漏 `kid` 后立即拒绝 | 全局安全事件 |

第一版不为普通 logout 建 access `jti` 黑名单，这是明确的成本/收益选择。若部署要求“退出设备后连被盗 access token 也立即失效”，应增加 session epoch/denylist 检查，不能仅把 access TTL 配长。

## 12. 授权模型

### 12.1 全局角色

`user.role` 为 `admin/user`：

- admin 可以管理用户并访问全部 service，但仍受领域不变量、step-up 和审计约束。
- user 只能访问通过 `mcp_service_permission` 显式授予的 service。
- 任何未匹配的路由、操作或权限默认拒绝。

### 12.2 团队角色

`team_membership.team_role` 为 `admin/member`，定义见 [01-data-model.md](01-data-model.md) 5.16/5.17。团队角色管理的是"团队本身"（成员、归属服务列表），**不直接决定服务可见性**——可见性统一由 [01-data-model.md](01-data-model.md) 5.12 `mcp_service_permission` 的显式记录（`principal_type=user/team/everyone`）决定，团队边界不产生任何隐式授权：

- **team member**：可以在本团队下创建新服务（创建者仍按第 12.4 节自动获得该 service 的 creator editor，且默认附带一条 `principal_type=everyone` 的开放记录，见 01 §5.12）。team member 身份本身不授予对"本团队名下服务"的可见性——如果该服务的授权记录里没有把这个团队列为 `principal_type=team`、也没有 `everyone` 记录，团队其他成员一样看不到，这是有意的设计：team 只是组织归属，不是访问白名单。
- **team admin**：在 team member 权限基础上，可以管理本团队成员的加入/移出/角色，可以把本团队下的服务转移到其他 active team（转移只改 `mcp_service.team_id`，不影响 `mcp_service_permission` 里已有的授权记录），可以代表团队为本团队名下的服务批量增删 `principal_type=team/everyone` 的授权记录（仍受第 12.4 节 creator editor 不变量约束，不能移除 creator 也不能把已有 `editor` 记录改成 `team`/`everyone` 角色）。team admin 不因此获得全局角色，不能创建/禁用用户，不能访问未被授权给自己团队的其他服务。
- 用户可以同时属于多个 team，也可以不属于任何 team；不属于任何 team 的用户依然可以看到所有带 `principal_type=everyone` 记录的服务，以及被显式授予个人权限的服务。
- 全局 `admin` 不受 team 边界限制，可管理全部 team 和其下全部服务，但仍受 step-up、乐观锁和审计约束，不绕过状态机。

### 12.3 Service 角色矩阵

| 操作 | viewer | editor | admin |
|---|---:|---:|---:|
| 查看服务、工具和脱敏配置 | 允许 | 允许 | 允许 |
| 查看脱敏 API Key 元数据 | 允许 | 允许 | 允许 |
| 查看服务级审计摘要 | 可选允许，按 API 契约固定 | 允许 | 允许 |
| 查看完整脱敏构建日志 | 禁止 | 允许 | 允许 |
| 创建/修改配置 revision | 禁止 | 允许 | 允许 |
| 触发构建、同步、发布和回退 | 禁止 | 允许 | 允许 |
| 生成、配置和吊销 API Key | 禁止 | 允许 | 允许 |
| 修改服务成员权限 | 禁止 | 允许 | 允许 |
| 删除、恢复或启停服务 | 禁止 | 允许 | 允许 |
| 创建用户、禁用用户、修改全局角色 | 禁止 | 禁止 | 允许 |
| 查看全局审计 | 禁止 | 禁止 | 允许 |

第一版固定 viewer 可以查看服务级审计摘要，但不能读取包含受限构建信息的详情。不论 viewer 来自 `principal_type=user`、`team` 还是 `everyone` 哪一种记录，在本矩阵中权限完全等同，不单独放宽或收紧。

### 12.4 对象级授权规则

- 每个 service 请求根据 path/body 中的 service ID 查询权限，不能只检查"用户拥有任意 service 权限"；可见性判定完全按 [01-data-model.md](01-data-model.md) 5.12 的 `visible(user, service)` 公式执行——即存在归属于当前用户的 `principal_type=user` 记录，或存在 `principal_type=everyone` 记录，或存在归属于当前用户所在团队的 `principal_type=team` 记录，三者任一成立即可见；写操作只认当前用户的 `principal_type=user AND role=editor` 记录（或全局 admin）。
- 列表和统计查询在 SQL 层加入授权过滤；禁止先加载全量数据再由前端隐藏。
- 对用户不可见的资源返回 404，避免枚举；资源可见但动作不允许时返回 403。
- 创建服务的事务必须同时授予 creator editor；creator editor 不可移除，见 [01-data-model.md](01-data-model.md)。
- 权限批量替换在单一事务内校验操作者、目标用户、creator 不变量和最后管理者不变量，并写字段级审计摘要。
- admin 的“绕过”只绕过 `mcp_service_permission` 查询，不绕过资源状态机、乐观锁、秘密脱敏、step-up 或审计。

## 13. 接口契约

| 方法与路径 | 认证 | 作用 |
|---|---|---|
| `POST /api/v1/auth/login` | 匿名 + 限速/Origin | 登录，返回 access，设置 refresh Cookie |
| `POST /api/v1/auth/refresh` | refresh Cookie + CSRF 防护 | 原子轮换并返回新 access |
| `POST /api/v1/auth/logout` | access + refresh Cookie | 删除当前 refresh session并清 Cookie |
| `POST /api/v1/auth/logout-all` | access | 删除当前用户全部 refresh session |
| `GET /api/v1/auth/me` | access | 返回数据库当前用户摘要 |
| `POST /api/v1/auth/change-password` | access + 当前密码 | 修改密码并使全部会话失效 |
| `POST /api/v1/auth/reauth` | access + 当前密码/MFA | 返回短期 step-up token |

统一错误结构沿用 `core/errors.py`。主要状态码：

- 400：请求格式错误。
- 401：凭据缺失、无效、过期或登录失败；带 `WWW-Authenticate: Bearer`。
- 403：已认证但无操作权限，或 CSRF/Origin 检查拒绝。
- 404：对象对当前用户不可见。
- 409：并发修改或领域状态冲突。
- 429：登录/接口限速，带 `Retry-After`。
- 503：数据库、Redis 或密钥服务不可用且安全状态无法确认。

错误详情禁止回显 JWT 库异常、密码哈希参数、Redis key、数据库堆栈和权限 SQL。

## 14. 敏感操作和 Step-up

以下操作要求最近 5 分钟内完成重新认证：

- 修改自己的密码。
- 创建、禁用、启用用户或修改全局角色。
- 管理员重置他人密码。
- 删除/恢复服务、批量替换权限。
- 关闭 Agent 鉴权、轮换秘密或执行高影响回退。

`POST /api/v1/auth/reauth` 验证当前密码；启用 MFA/OIDC 后使用同等或更强因素。成功返回只存内存的短期 JWT，`typ=stepup+jwt`，包含 `sub/sid/auth_time/iat/exp`，有效期最多 5 分钟，只能作为 `X-LiteMCP-Step-Up` Header 发送。step-up token 使用与 access 明确区分的 `typ/aud`，禁止两者互相替代。

敏感操作执行时仍需重新做普通认证、当前角色和对象权限检查；step-up 只证明最近重新认证，不授予额外权限。

## 15. 公网、MFA 与企业身份

第一版密码认证适用于内网、VPN 或有反向代理访问控制的部署。如果管理端直接暴露到互联网，生产基线必须至少启用一种额外强认证控制：

- WebAuthn/passkey 或 TOTP MFA；或
- 企业 OIDC，通过 BFF 模式让 IdP access/refresh token 保留在服务端；浏览器只持有 LiteMCP 的 HttpOnly session Cookie。

后续 OIDC 不能把 IdP refresh token 放入 React 或 localStorage。外部身份的 subject 与本地 `user` 显式绑定，本地 `role/status/mcp_service_permission` 仍是 LiteMCP 授权真源；IdP 账号停用和组同步策略需要单独设计。

## 16. Redis 和依赖故障

- Redis 不可用时，login 和 refresh fail-closed，返回 503；不得签发未登记的 refresh token。
- 已有 access token 仍可在自身有效期内使用，但每个请求继续查询数据库用户状态和权限。
- logout 先清除浏览器 Cookie；若 Redis 删除失败，返回 503并记录高优先级审计/指标，不能静默宣称服务端会话已吊销。
- 简单部署允许 Redis 重启导致全部 refresh session 失效并要求重新登录；不得从可能包含已吊销会话的过旧备份恢复 session key。
- 生产 Redis 使用独立 namespace、认证和 TLS/受信网络；会话 key 设置 TTL，并避免被普通缓存的 eviction 策略意外淘汰。
- 数据库不可用时管理 API 认证与授权 fail-closed。
- 系统时钟必须通过 NTP 同步；发现超出 JWT 允许偏差时告警，而不是扩大 token 容忍窗口。

## 17. 审计、日志与指标

至少记录以下 `audit_event.action`：

- `auth.login.success` / `auth.login.denied`
- `auth.account.locked` / `auth.account.unlocked`
- `auth.session.created` / `auth.session.refreshed`
- `auth.refresh_reuse_detected`
- `auth.logout` / `auth.logout_all`
- `auth.password.changed` / `auth.password.reset`
- `auth.reauth.success` / `auth.reauth.denied`
- `auth.authorization.denied`
- `user.created` / `user.status_changed` / `user.role_changed`
- `service.permission.changed`

审计包含 request ID、actor、目标资源、结果、稳定 reason code、可信来源 IP、清洗后的 User-Agent 和非敏感变更摘要。匿名登录失败可记录 username 的带 pepper HMAC 以便关联，但不得记录原始密码、完整 Token、Cookie、Authorization Header、Token 摘要输入或 CSRF secret。

核心指标包括登录成功/失败数、锁定数、refresh 成功/失败/重放数、401/403 数、Redis 认证存储错误、step-up 失败和鉴权延迟。指标标签禁止使用 username、user ID、session ID、IP 等高基数或个人数据。

## 18. 配置项和安全默认值

| 配置 | 默认 | 约束 |
|---|---:|---|
| `ADMIN_ACCESS_TTL_SECONDS` | 900 | 生产建议 600–1800 |
| `ADMIN_REFRESH_IDLE_TTL_SECONDS` | 28800 | 必须小于 absolute TTL |
| `ADMIN_REFRESH_ABSOLUTE_TTL_SECONDS` | 604800 | 不可滚动延长 |
| `ADMIN_LOGIN_FAILURE_THRESHOLD` | 5 | 必须配合观察窗口 |
| `ADMIN_LOGIN_FAILURE_WINDOW_SECONDS` | 900 | 数据库/Redis 统一语义 |
| `ADMIN_LOCK_SECONDS` | 900 | 可改为受上限约束的指数退避 |
| `ADMIN_JWT_ISSUER` | 部署显式配置 | 不允许从请求 Host 推导 |
| `ADMIN_JWT_AUDIENCE` | `litemcp-admin-api` | 与 Agent audience 分离 |
| `ADMIN_JWT_ALGORITHM` | `HS256` | 固定 allowlist，不接受 Token 自选 |
| `ADMIN_JWT_CLOCK_SKEW_SECONDS` | 30 | 不得用大偏差掩盖时钟问题 |
| `ADMIN_REAUTH_TTL_SECONDS` | 300 | 不超过 5 分钟 |
| `ADMIN_ALLOWED_ORIGINS` | 部署显式配置 | 生产不允许 `*` |

所有秘密配置必须通过受保护环境或 Secret Manager 注入；`.env.example` 只给格式，不提供可用于生产的默认密钥。启动时检查密钥长度、默认值、Origin 和 Cookie 安全配置，不安全的生产配置应拒绝启动。

## 19. 验收清单

- access token 只存在于前端内存；浏览器存储、URL、日志和错误中均无 Token。
- refresh Cookie 的 name/path/HttpOnly/Secure/SameSite/Max-Age 符合本篇定义。
- JWT 拒绝 `alg=none`、错误算法、错误 `kid/typ/iss/aud`、过期/未生效 Token 和类型异常 Claims。
- refresh 每次成功都轮换；旧 token 再用会吊销 session 并产生审计告警。
- 同一浏览器多请求、多 Tab 刷新不会正常触发重放；服务端原子测试证明只能有一个旧 token 成功。
- 用户禁用、密码修改、全局角色和 service 权限修改在下一请求按第 11 节生效。
- 普通登出、全部登出、refresh 过期和 Redis 重启行为符合明确契约。
- 登录不存在用户、密码错误、disabled 和 locked 的 HTTP 状态、响应体及显著时序一致，不泄漏账号状态。
- 并发失败登录不会丢计数；观察窗口、锁定到期、手动解锁和 disabled 优先级均有测试。
- viewer/editor/admin 权限矩阵覆盖列表、详情和所有写操作，越权对象不能通过猜测 ID 访问。
- Origin/CORS/Fetch Metadata 防护拒绝跨站 login、refresh 和 logout；生产只接受 HTTPS。
- Redis 或数据库不可用时不会 fail-open，也不会签发无法吊销的 refresh 会话。
- 密码、Token、Cookie、Authorization Header 和秘密不进入 audit、普通日志、metrics 或 trace。
- PostgreSQL 与 MySQL 都通过用户状态并发、权限事务和 `password_changed_at` 失效测试。

实现测试还应并入 [09-verification.md](09-verification.md) 的后端、前端和端到端测试矩阵。

## 20. 参考基线

- [OWASP Authentication Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Authentication_Cheat_Sheet.html)
- [OWASP Session Management Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html)
- [OWASP Authorization Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Authorization_Cheat_Sheet.html)
- [OWASP Password Storage Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html)
- [OWASP CSRF Prevention Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html)
- [RFC 8725 · JSON Web Token Best Current Practices](https://www.rfc-editor.org/rfc/rfc8725.html)
- [RFC 9700 · Best Current Practice for OAuth 2.0 Security](https://www.rfc-editor.org/rfc/rfc9700.html)
- [NIST SP 800-63B · Authentication and Authenticator Management](https://pages.nist.gov/800-63-4/sp800-63b.html)
