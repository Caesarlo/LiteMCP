# ADR-0005: StorageBackend 抽象 + 每 revision 不可变 OCI 镜像、按 digest 运行，数据库只存引用

- Status: Accepted
- Date: 2026-08-09
- Source refs: docs/architecture/00-overview.md (L143, L151, L180); docs/architecture/01-data-model.md (L26); docs/architecture/04-stdio-sandbox.md §2 (L37-L42); docs/architecture/08-implementation-plan.md (L120, L307, L422); docs/architecture/09-verification.md (L48, L142, L265); docs/architecture/07-observability.md (L419)

## Context

代码包、服务 descriptor、构建日志、构建产物和容器镜像需要持久化存储，并且这些制品需要被 worker 安全地构建、缓存、运行和垃圾回收。需要决定：（a）存储层是否要为开发/生产使用不同后端并如何统一抽象；（b）用户上传的代码包如何变成可运行的隔离环境；（c）数据库与对象存储/镜像仓库之间的信任边界。

## Decision

1. 引入统一的 `StorageBackend` 接口：本地开发使用文件系统实现，生产使用 S3/MinIO；容器镜像进入 OCI Registry（00-overview.md L143, L180）。数据库**只保存**不可变对象 key、摘要（digest）和元数据，不保存制品本身，也不作为制品真源（00-overview.md L151；01-data-model.md L26）。
2. STDIO 沙箱运行方案采用**每 revision 构建不可变 OCI image，按 digest 运行**，这是 04-stdio-sandbox.md §2 比较表中标记为"**[决定] 生产方案**"的选项（L37）。
3. 生产环境将构建成功的产物推送到受控 OCI Registry；`service_artifact(kind=container_image)` 保存 manifest digest（`sha256:...`）和平台；runner 只按 digest 拉取运行，**禁止**按可变 tag 运行。单机开发可以把镜像保存在专用 rootless Docker daemon 的本地 image store，但数据库仍保存 image ID/digest，节点迁移前必须重新构建或推送 Registry；共享 volume 只用于构建临时目录，不能作为可发布产物真源（04-stdio-sandbox.md L40）。
4. 构建缓存 key 由 `base_image_digest + python_abi + target_platform + dependency_lock_digest + builder_version` 组成；`service_id + requirements hash` 只能作为人类可读 tag，**不能**作为安全身份使用（04-stdio-sandbox.md L42）。
5. StorageBackend 具备 staging/available/quarantine/GC 状态机（08-implementation-plan.md L120）；对象存储/Registry 不可用时，已缓存的 digest 仍可运行，但新的 build/pull 必须 fail-closed，而不是静默降级（08-implementation-plan.md L422）。
6. **拒绝的替代方案**（04-stdio-sandbox.md L37 比较表明确拒绝）：宿主子进程/venv 直接运行用户代码（隔离性不足，拒绝）；单一共享容器 + 挂载每个 service 源码（跨租户隔离性不足，拒绝）。microVM/gVisor/Kata 等沙箱容器运行时被列为 **[建议] 高风险部署增强**，不是首期方案。

## Consequences

- 正面：不可变、digest 寻址的镜像天然可审计、可回滚、可扫描漏洞；数据库从不信任外部 URL 或可变 tag，安全边界清晰（DB 只存引用，制品真源在 Registry/StorageBackend）。
- 负面：需要维护 Registry/对象存储的可用性、容量和 GC 策略；开发/生产存储后端不同（文件系统 vs S3/MinIO），需要通过接口测试保证行为一致（09-verification.md L142 要求对 filesystem/S3-compatible fake 都执行摘要、staging/available、失败清理与不可变对象契约测试）；worker 就绪检查必须显式包含 StorageBackend 和 rootless Docker socket/executor（07-observability.md L419）。
- 故障降级联动：Registry/对象存储失联时，已缓存 digest 可继续运行，但新 build/pull 必须 fail-closed 并触发容量/失联告警，这一行为需要在故障演练中验证（08-implementation-plan.md L307, L422）。
- **[建议，暂缓]**：microVM/沙箱容器运行时（gVisor/Kata）——触发条件是"高风险部署场景"出现且现有 rootless Docker + digest 运行方案的隔离强度不足以满足该场景要求。
- 重新评估条件：出现 rootless Docker 无法满足的强隔离需求，或 Registry/对象存储供应商变更导致 StorageBackend 接口契约需要扩展时，需要修订本 ADR。
