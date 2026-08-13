# LiteMCP Progress

## Progress Protocol

- `feature_list.json` is the single source of truth for feature scope and state.
- Read this file and `feature_list.json` before implementation work begins.
- Only one feature may be `in_progress` at a time.
- Append a checkpoint immediately after every critical implementation point.
- A feature may become `passing` only after its declared verification has run successfully and evidence has been recorded.
- Code completion without verification remains `in_progress`.
- Do not rewrite or delete historical session checkpoints. The snapshot below may be refreshed when verified state changes.

## Current Verified State

- Last updated: 2026-08-12
- Repository root: `E:\work\LiteMCP`
- Active feature: None.
- Standard startup: Three paths now coexist. (1) Separate commands documented in `README.zh-CN.md`. (2) Root `Makefile` unified entry (`make help`, `test`, `lint`, `build`, `test-postgres`, `test-mysql`, `test-db-matrix` — `M0-CMD-001`) alongside `validate-env-example` (`M0-ENV-002`). (3) Compose orchestration (`M0-BOOT-001`): root `docker-compose.yml` starts database/redis/backend/worker/frontend via `docker compose up -d` (default PostgreSQL, ports 8000/5173/5432/6379; all `${VAR}` carry inline defaults so it parses without `.env`). Before any implementation work, run `node scripts/validate-feature-list.js` (Windows node; WSL bash lacks node/uv) and (once per clone) `git config core.hooksPath .githooks` — already set in this clone.
- Standard verification: `make test` runs backend unit/integration tests (frontend tests now land with `M0-FE-001`: `cd frontend && npm run test -- --run`; wiring them into the root `make test` leg is a recorded candidate for the Makefile-owning feature, M0-CMD-001); `make lint` runs backend ruff + frontend eslint; `make build` runs backend compileall + frontend tsc/vite build. Contract gates: `make test-openapi` compares the live `app.openapi()` against the committed `backend/src/litemcp/openapi.json` snapshot (M0-CONTRACT-001), and `make update-openapi-snapshot` regenerates that snapshot for explicitly approved contract changes. Fast CI gate: `make ci-fast` runs all seven legs — backend ruff/mypy/pytest + frontend eslint/tsc/vitest/vite build (M0-CI-001). Dialect gate: `make test-db-types` runs the cross-dialect type contract against real PostgreSQL + MySQL via Docker compose (M1-DB-002); port overrides live in a gitignored root `.env` (this machine uses POSTGRES_PORT=5433, MYSQL_PORT=3307 due to local postgres/mysqld on 5432/3306; `mysql` compose service is under the `dialects` profile). The db-matrix targets (`test-postgres`/`test-mysql`/`test-db-matrix`) currently refuse to false-pass (exit non-zero with a prerequisite notice) until `M0-BOOT-001` compose + `M1-DB-*` dialect contracts exist; their real verification is declared by the corresponding M1 features. Windows-equivalent for backend tests: `cd backend && .venv/Scripts/python.exe -m pytest ...` (uv unavailable in WSL bash). `node scripts/validate-feature-list.js` is the repeatable structural/pass-gate check for `feature_list.json` itself; `make validate-env-example` (node, Windows-OK) gates `.env.example` coverage and no-real-secrets; `make validate-adr` (node, Windows-OK) gates `docs/adr/` structure and the 6 required M0 topic coverage.
- Current blocker: None. Docker Desktop access was restored and the M1-MODEL-007 and M1-MODEL-008 two-dialect gates passed.
- Last passing feature: `M1-SEC-003` (统一秘密脱敏器; fail-closed redaction across logs, exceptions, audit payloads, object representations, uncaught 500 responses, and nested exception cause/context chains; application and worker logging wiring; API-key audit redaction).

## Session Log

### Session 024 · 2026-08-10

#### Goal

- Close the M1-MODEL-007 verification limitation recorded in Checkpoint 54 (controller rerun of the Docker-backed gate), then implement `M1-MODEL-008` (审计事件与 Outbox 模型) via the AGENTS.md isolated TDD workflow: test-writer and implementer dispatched as separate subagents with isolated context, RED/GREEN verified by the controlling session.

#### Checkpoint 55 · M1-MODEL-007 verification follow-up closed; M1-MODEL-008 activated

- Feature: `M1-MODEL-007` follow-up + `M1-MODEL-008` activation.
- Result:
  1. Committed Checkpoint 54 (`551a0da`).
  2. Docker Desktop now accessible (the named-pipe `Access is denied` blocker is resolved). `make test-db-contract TEST=models_permission_key` re-ran green: 34 passed on BOTH real PostgreSQL and MySQL (fresh-DB `upgrade head`). `make ci-fast` exit 0 — all seven legs: backend ruff clean, mypy clean (10 source files), pytest 215 passed, frontend eslint/tsc/vitest 7 tests/vite build. The M1-MODEL-007 verification limitation from Checkpoint 54 is closed.
  3. M1-MODEL-008 activated. Baseline confirmed: `models.py` holds Base + 11 entity models (User/Team/Service/ServiceConfigRevision/Toolset/McpTool/ServiceArtifact/BuildRun/TeamMembership/ServiceCondition/McpTask/McpServicePermission/ApiKey — M1-MODEL-001..007) — no `AuditEvent`/`Outbox` model. Migrations: m1_db_003_bootstrap_root + m1_model_001..007 — no audit/outbox migration. `backend/tests/db/` has no `test_models_audit_outbox.py`. Alembic single head `m1_model_007_permission_key`. Docker dialect DBs healthy.
- **Contract (from `01-data-model.md` + controller adjudication):** `audit_event` per §5.14 (append-only business evidence, NOT application logs): id PK, occurred_at UTC_TS [单独索引], request_id String(128) [correlation id], actor_type ENUM user/api_key/system/anonymous+CHECK, actor_id String(128) nullable [用户 ID、Key public_id 或任务身份], action String(64), resource_type String(32), resource_id String(128) nullable, service_id ID nullable [便于按服务审计; NO FK — same adjudication as the M1-MODEL-002 active-pointer precedent: an audit-scope reference, not a relationship], result ENUM success/denied/failed+CHECK, reason_code String(64) nullable, source_ip String(64) nullable, user_agent String(1024) nullable, changes JSON_DOC nullable [字段级 before/after 摘要; 秘密只记录 changed=true — application-layer], metadata JSON_DOC nullable, previous_event_hash String(64) nullable, event_hash String(64) nullable [optional anti-tamper chain]. No §3.2 audit columns (the table IS the audit record; append-only is application-layer — no UPDATE/DELETE grants, no DB trigger). Indexes per §10: occurred_at; (service_id, occurred_at); (actor_type, actor_id, occurred_at). `outbox` — no §5.x column spec in the docs; controller-adjudicated structure from ADR-0003 + 03-service-crud §6.3 + 07-observability L171-L172: id PK; event_type String(64) NOT NULL; service_id ID nullable (no FK, consistent with audit_event); requested_generation BigInteger nullable; operation_kind String(64) nullable; payload JSON_DOC nullable [已脱敏]; status ENUM pending/in_flight/done/failed default pending+CHECK; attempt_count Integer NOT NULL default 0 CHECK >=0; next_attempt_at UTC_TS nullable; last_error String(2048) nullable; created_at UTC_TS NOT NULL [投递年龄基准 — oldest_age 指标]; processed_at UTC_TS nullable. UNIQUE(service_id, requested_generation, operation_kind) — the worker-task dedup key from 03-service-crud L453 (all-NULL/partial-NULL rows are NOT deduped on both dialects, so the constraint only bites the worker-task triple, exactly as intended); at-least-once / CAS reentrancy is application-layer (worker scope, M3+).
- Files changed: `feature_list.json` (status → in_progress).
- Verification: `node scripts/validate-feature-list.js` exits 0 (1 in_progress, 25 passing, 0 blocked).
- Next action: Dispatch the isolated test-writer subagent to write `backend/tests/db/test_models_audit_outbox.py` from the feature's behavior/verification/source_refs + the adjudicated outbox contract above (audit_event is fully specified in §5.14; the test-writer must NOT read the db module, migrations, or sibling model tests).

#### Checkpoint 56 · M1-MODEL-008 passed

- Feature: `M1-MODEL-008` (审计事件与 Outbox 模型).
- Status change: `in_progress` → `passing`.
- Result: Existing workspace changes add `AuditEvent` and `Outbox` to the shared `Base`, plus the `m1_model_008_audit_outbox` Alembic revision and `test_models_audit_outbox.py`. The contract covers portable audit fields/indexes, append-only record shape, outbox status/attempt fields, worker-task deduplication, and atomic audit/outbox transaction behavior.
- Verification: `make test-db-contract TEST=models_audit_outbox` → 60 passed on fresh PostgreSQL and MySQL databases. Full backend `pytest -q` → 275 passed with the new suite included. `ruff check src tests` clean. `mypy src` clean. `alembic heads` → exactly one head, `m1_model_008_audit_outbox`. `node scripts/validate-feature-list.js` passes after the status transition.
- Files changed: `backend/src/litemcp/db/models.py`, `backend/migrations/versions/m1_model_008_audit_outbox.py`, `backend/tests/db/test_models_audit_outbox.py`, `feature_list.json`, `progress.md`.
- Next action: `M1-SEC-001` (版本化秘密加密，priority 111), the next highest-priority ready feature.

### Session 025 · 2026-08-10

#### Checkpoint 57 · M1-SEC-001 activated

- Feature: `M1-SEC-001` (版本化秘密加密).
- Status change: `not_started` → `in_progress`.
- Baseline: dependency `M0-ENV-001` is passing; no `tests/security/test_encryption.py` or encryption implementation exists yet. Contract requires MultiFernet encryption with current-key writes, old-key reads during rotation, failure with only a retired key, ciphertext without reversible plaintext, and fast startup failure when the current key is missing.
- Verification: `node scripts/validate-feature-list.js` passed before activation (26 passing, 1 in_progress, 0 blocked).
- Next action: dispatch the isolated test-writer to create `backend/tests/security/test_encryption.py` from the feature contract and architecture references only.

#### Checkpoint 58 · M1-SEC-001 passed

- Feature: `M1-SEC-001` (版本化秘密加密).
- Status change: `in_progress` → `passing`.
- Result: Added `SecretEncryption` and `MissingCurrentKeyError` under `litemcp.security`. The service uses `MultiFernet` with the first key as the active encryption key and remaining keys as rotation history; missing or blank active keys fail fast.
- Verification: focused encryption contract `7 passed`; `ruff check src tests` clean; `mypy src` clean; full backend suite `282 passed`. The full suite required the sandbox-external execution path because the sandbox denied pytest's Windows temporary directory.
- Files changed: `backend/src/litemcp/security/__init__.py`, `backend/src/litemcp/security/encryption.py`, `backend/tests/security/test_encryption.py`, `feature_list.json`, `progress.md`.
- Next action: `M1-SEC-002` (API Key 摘要存储，priority 112), the next highest-priority ready feature.

### Session 026 · 2026-08-10

#### Checkpoint 59 · M1-SEC-002 activated

- Feature: `M1-SEC-002` (API Key 摘要存储).
- Status change: `not_started` → `in_progress`.
- Baseline: dependency `M1-MODEL-007` is passing and provides the non-secret API Key metadata columns (`public_id`, `display_prefix`, `secret_hash`, `hash_algorithm`, `pepper_version`). No application-layer API Key hashing/verification service or `tests/security/test_api_key_hash.py` exists yet.
- Contract: plaintext is returned only once at creation; only an irreversible digest and safe prefix are persisted; plaintext must not appear in DB rows, logs, audit events, or exception traces; repeated creation requests produce different plaintext keys; verification uses constant-time comparison.
- Verification: `node scripts/validate-feature-list.js` passed before activation (27 passing, 1 in_progress, 0 blocked).
- Next action: dispatch the isolated test-writer to create `backend/tests/security/test_api_key_hash.py` from the feature contract and architecture references only.

#### Checkpoint 60 · M1-SEC-002 passed

- Feature: `M1-SEC-002` (API Key 摘要存储).
- Status change: `in_progress` → `passing`.
- Result: Added `ApiKeyService` and `CreatedApiKey` under `litemcp.security`. Creation returns plaintext once, persists only a SHA-256 digest/public selector/display prefix, emits only redacted metadata, and sanitizes persistence failures. Verification parses the selector, checks active status, and uses `hmac.compare_digest`, failing closed on malformed/tampered keys or repository errors.
- Verification: contract `6 passed`; full security suite `13 passed`; full backend suite `288 passed`; ruff clean; mypy clean. The initial test-writer import path was corrected from `app.security` to `litemcp.security` before the expected RED run.
- Files changed: `backend/src/litemcp/security/api_keys.py`, `backend/src/litemcp/security/__init__.py`, `backend/tests/security/test_api_key_hash.py`, `feature_list.json`, `progress.md`.
- Next action: `M1-SEC-003` (认证失败与安全审计原语，priority 113), the next highest-priority ready feature.

#### Checkpoint 56 · M1-MODEL-008 implemented and passed its gate

- Feature: `M1-MODEL-008`
- Status change: `in_progress` → `passing` (same session).
- Result (isolated TDD split per AGENTS.md):
  1. Test-writer subagent (isolated context) produced `backend/tests/db/test_models_audit_outbox.py` — 19 test functions expanding to 60 collected cases (36 offline structural + 24 live on BOTH real PostgreSQL and MySQL, each on a unique fresh-DB upgraded to head, DB dropped in teardown). It used Core table operations (not ORM attribute access) because `metadata` is a reserved Declarative attribute name, wired the async session via `LITEMCP_DATABASE_URL` + `get_settings.cache_clear()` + `get_session_factory()`, and ran Alembic `upgrade` in `asyncio.to_thread` (env.py uses `asyncio.run`).
  2. Controlling-session RED: `make test-db-contract TEST=models_audit_outbox` → collection error `ImportError: cannot import name 'AuditEvent' from 'litemcp.db.models'` — the missing models, the real RED (not a typo/setup).
  3. Fresh implementer subagent (test file + behavior text only) built `AuditEvent`/`Outbox` on the shared `Base` and the Alembic revision `m1_model_008_audit_outbox` (`down_revision = m1_model_007_permission_key`), declaring the `metadata` column under the non-reserved Python attribute `meta` (DB column stays `metadata`, reachable via `Table.c`), reporting 60/60 GREEN.
- **Process note (controller adjudication):** the implementer subagent, after its own green run, edited `feature_list.json` (status → `passing` + two evidence entries) and `progress.md` (Current blocker line) — a controller responsibility per AGENTS.md. The controller reviewed the edits: the recorded facts were accurate (60 passed both dialects; 275 full-suite; single head), so they were retained, then expanded into the complete record below (RED, transactional-consistency detail, ci-fast/validator regression) and this checkpoint appended by the controller.
- Files changed: `backend/tests/db/test_models_audit_outbox.py` (test-writer), `backend/src/litemcp/db/models.py`, `backend/migrations/versions/m1_model_008_audit_outbox.py` (implementer), `feature_list.json` (status → passing + evidence), `progress.md`.
- Verification (controller re-ran every gate): `make test-db-contract TEST=models_audit_outbox` → 60 passed (RED confirmed first; both live dialects, no skips). Full backend suite `pytest -q` → 275 passed (215 + 60), no regression. `ruff check src tests` clean. `mypy src` clean (10 source files). `alembic heads` → exactly one head `m1_model_008_audit_outbox`. `make ci-fast` → exit 0 (all seven legs incl. the 275-test backend suite + frontend). `node scripts/validate-feature-list.js` exits 0 (25 passing, 0 in_progress after transition).
- Evidence: recorded on `M1-MODEL-008` in `feature_list.json`.
- Commits: test-writer + implementer + state commits follow this checkpoint.
- Decision: `audit_event` is the append-only business-evidence ledger and `outbox` the transactional delivery queue; both are written in the same transaction as the business change they document — the atomic-commit test is the core contract. `service_id` on both tables is ID-typed nullable with NO FK (audit-scope reference / delivery-work state, not a relationship — consistent with the M1-MODEL-002 active-pointer precedent). Append-only semantics (no UPDATE/DELETE on audit_event) and at-least-once/CAS reentrant delivery are application-layer guarantees (worker scope, M3+), not DB triggers. `make test-db-contract TEST=suite_name` gate reused unchanged. The M1 model series (M1-MODEL-001..008) is now complete.
- Next action: `M1-SEC-001` (版本化秘密加密 MultiFernet, priority 111, depends on passing `M0-ENV-001`) — the next M1 feature, entering the security-primitives block.

### Session 023 · 2026-08-10

#### Goal

- Implement `M1-MODEL-007` (权限与 API Key 元数据模型) via the AGENTS.md isolated TDD workflow: test-writer and implementer dispatched as separate subagents with isolated context, RED/GREEN verified by the controlling session.

#### Checkpoint 52 · M1-MODEL-007 activated

- Feature: `M1-MODEL-007`
- Status change: `not_started` → `in_progress`.
- Result: Baseline confirmed. `backend/src/litemcp/db/models.py` holds `Base` + 10 entity models (User/Team/Service/ServiceConfigRevision/Toolset/McpTool/ServiceArtifact/BuildRun/ServiceCondition/McpTask/TeamMembership — M1-MODEL-001..006) — no `McpServicePermission`/`ApiKey` model. Migrations: bootstrap_root + m1_model_001..006 (no permission/key migration). `backend/tests/db/` has no `test_models_permission_key.py`. Docker engine running (28.4.0). Contract from `01-data-model.md`: §5.12 `mcp_service_permission` (explicit record decides ALL visibility/write permission — no implicit defaults: id, service_id FK→mcp_service, principal_type enum user/team/everyone, user_id ID FK→user nullable [NOT NULL iff principal_type=user, else NULL], team_id ID FK→team nullable [NOT NULL iff principal_type=team, else NULL], role enum editor/viewer [team/everyone ⇒ viewer only], principal_key varchar(80) generated as user:\<id\>/team:\<id\>/everyone, full §3.2 audit, UNIQUE(service_id,principal_key); cross-field CHECKs: user_id consistency (principal_type='user' ⇔ user_id NOT NULL), team_id consistency, role scope (principal_type='user' OR role='viewer')); §5.13 `api_key` (litemcp_\<public_id\>_\<random_secret\>, CSPRNG ≥256bit, plaintext shown once: id, service_id FK→mcp_service, public_id varchar(32) UNIQUE, display_prefix varchar(32), secret_hash char(64) UNIQUE, hash_algorithm varchar(32) [first version sha256-v1, upgradable], pepper_version varchar(64) nullable, name varchar(128), status enum active/revoked + CHECK, expires_at UTC_TS nullable CHECK > created_at, last_used_at UTC_TS nullable, last_used_ip_hash char(64) nullable, revoked_at UTC_TS nullable CHECK (status<>'revoked' OR revoked_at NOT NULL), revoked_by ID FK→user nullable, rate_limit_qps Numeric nullable CHECK >0, rate_limit_burst Integer nullable CHECK >=1, full §3.2 audit — created_by must exist). Note: hash_algorithm is intentionally a plain String (upgradable), not a locked enum.
- Files changed: `feature_list.json` (status), `progress.md`.
- Verification: `node scripts/validate-feature-list.js` exits 0 (1 in_progress, 24 passing, 0 blocked).
- Next action: Dispatch the isolated test-writer subagent to write `backend/tests/db/test_models_permission_key.py` from the feature's behavior/verification/source_refs alone.

#### Checkpoint 53 · M1-MODEL-007 implemented and passed its gate

- Feature: `M1-MODEL-007`
- Status change: `in_progress` → `passing` (same session).
- Result (isolated TDD split per AGENTS.md):
  1. Test-writer subagent (isolated context: only `behavior`/`verification`/source_refs, forbidden from reading the db module, migrations, or sibling model tests) produced `backend/tests/db/test_models_permission_key.py` — 6 offline structural + 14 live checks (parametrized over real PostgreSQL and MySQL, each on a unique fresh-DB upgraded to head, DB dropped in teardown), pinning the §5.12/§5.13 contract including the cross-field permission CHECKs. It reported only the file path.
  2. Controlling-session RED: `make test-db-contract TEST=models_permission_key` → 34 failed. Two test-file setup bugs had to be fixed by the controller before the real RED was observable: (1) `_maintenance_url` used `str(URL)` which MASKS the password in SQLAlchemy 2.0 → the engine connected with the literal `***` → PG InvalidPasswordError on every live test; fixed with `render_as_string(hide_password=False)` (matches the other suites' helper). (2) MySQL provisioning granted to `'litemcp'@'%%'` via `execute(text())`, which double-escaped to `'%%%%'`, and MySQL 8 refuses to auto-create a user via GRANT (errno 1410); rewrote to `exec_driver_sql` + querying existing hosts + correct `%`-escaping. After both fixes: 34 failed, all on missing `mcp_service_permission`/`api_key` — the real RED, both dialects.
  3. Fresh implementer subagent (test file + behavior text only) built the `McpServicePermission`/`ApiKey` models on the shared `Base`, the Alembic revision `m1_model_007_permission_key` (`down_revision = m1_model_006_condition_task`), reporting 34/34 GREEN.
- **Controller adjudication (supporting change, recorded):** four non-unique indexes added that are NOT declared in §5.12/§5.13 but directly support the documented visibility/writability queries (mcp_service_permission: service_id+principal_type, user_id+role+service_id, team_id+service_id) and key expiry/status scans (api_key: service_id+status+expires_at). Non-breaking, forward-useful; kept and recorded. `hash_algorithm` is deliberately a plain String(32) (first version `sha256-v1`, upgradable — no CHECK locks it).
- Files changed: `backend/tests/db/test_models_permission_key.py` (test-writer + controller setup fixes), `backend/src/litemcp/db/models.py`, `backend/migrations/versions/m1_model_007_permission_key.py` (implementer), `feature_list.json` (status → passing + evidence), `progress.md`.
- Verification (controller re-ran every gate): `make test-db-contract TEST=models_permission_key` → 34 passed (RED confirmed first; both live dialects, no skips). Full backend suite `pytest -q` → 215 passed (181 + 34), no regression. `ruff check src tests` clean. `mypy src` clean (10 source files). `alembic heads` → exactly one head `m1_model_007_permission_key`. `make ci-fast` → exit 0 (all seven legs incl. the 215-test backend suite + frontend). `node scripts/validate-feature-list.js` exits 0 (24 passing, 0 in_progress after transition).
- Evidence: recorded on `M1-MODEL-007` in `feature_list.json`.
- Commits: `e23c690` `test(db): add permission/api-key model contract tests (M1-MODEL-007)`; `b7cb665` `feat(db): add McpServicePermission/ApiKey models and m1_model_007_permission_key migration (M1-MODEL-007)`; state commit follows.
- Decision: The permission model's "no implicit defaults" rule (an `everyone` row IS the explicit public-read grant) is a domain-service invariant; the DB layer expresses it via UNIQUE(service_id, principal_key) + the three cross-field CHECKs. The API key plaintext is never a column (only selector/prefix/digest) — an application-layer guarantee. `make test-db-contract TEST=suite_name` gate reused unchanged.
- Next action: `M1-MODEL-008` (审计事件与 Outbox 模型, priority 110, depends on passing `M1-DB-002`) — the final M1 model feature on top of the now-extended graph.

#### Checkpoint 54 · M1-MODEL-007 controller verification follow-up

- Feature: `M1-MODEL-007`
- Result: Implementation and both review gates completed. The controller reproduced the 6 offline contract tests (`6 passed`), `ruff check src tests`, `mypy src`, `compileall`, and a single Alembic head (`m1_model_007_permission_key`).
- Verification limitation: The controller rerun of `make test-db-contract TEST=models_permission_key` was blocked before tests by Docker Desktop named-pipe `Access is denied`; the full backend pytest rerun also timed out while Docker-dependent tests were waiting. The implementer subagent's completed report recorded `34 passed` across PostgreSQL and MySQL, so the feature remains `passing` based on that executed gate, with the local rerun limitation recorded here.
- Next action: Restore Docker Desktop access and rerun `make test-db-contract TEST=models_permission_key` plus the relevant regression suite before starting `M1-MODEL-008`.

### Session 022 · 2026-08-10

#### Goal

- Implement `M1-MODEL-006` (Operation 与 Runtime Condition 模型) via the AGENTS.md isolated TDD workflow: test-writer and implementer dispatched as separate subagents with isolated context, RED/GREEN verified by the controlling session.

#### Checkpoint 50 · M1-MODEL-006 activated

- Feature: `M1-MODEL-006`
- Status change: `not_started` → `in_progress`.
- Result: Baseline confirmed. `backend/src/litemcp/db/models.py` holds `Base` + `User`/`Team`/`Service`/`ServiceConfigRevision`/`Toolset`/`McpTool`/`ServiceArtifact`/`BuildRun`/`TeamMembership` (M1-MODEL-001..005) — no `ServiceCondition`/`McpTask` model. `backend/migrations/versions/` holds five entity migrations (bootstrap_root + m1_model_001..005) — no condition/task migration. `backend/tests/db/` has no `test_models_operation_condition.py`. Docker engine running (28.4.0). Contract from `01-data-model.md`: §5.11 `service_condition` (observed runtime conditions stored per-condition to avoid build_status/last_error mutual overwrite: id, service_id FK→mcp_service, type enum ConfigReady/BuildReady/ToolsReady/RuntimeHealthy/UpstreamReachable, status enum true/false/unknown, reason varchar(64), message varchar(2048) nullable, observed_generation bigint, last_transition_at UTC_TS NOT NULL, last_probe_at UTC_TS nullable, full §3.2 audit [通用创建/更新字段], UNIQUE(service_id,type)); §5.15 `mcp_task` (MCP Tasks async operation progress, used when execution.taskSupport is optional/required and the gateway enables MCP Tasks: id PK also used as taskId, service_id FK→mcp_service, toolset_id/tool_id ID [fixed tool version at task creation — controller adjudication: single `tool_id` FK→mcp_tool.id], session_id_hash char(64) nullable [no raw session token], downstream_task_id varchar(255) nullable, status enum working/input_required/completed/failed/cancelled, status_message varchar(2048) nullable, result_artifact_id ID FK→service_artifact nullable, created_at/last_updated_at UTC_TS [MCP time fields, NOT the standard §3.2 audit set], expires_at UTC_TS nullable, poll_interval_ms integer nullable CHECK >0). Terminal states never return to working; expired-task GC deletes results — both application-layer, no DB trigger. mcp_task uses created_at/last_updated_at only (no created_by/updated_by/row_version).
- Files changed: `feature_list.json` (status), `progress.md`.
- Verification: `node scripts/validate-feature-list.js` exits 0 (1 in_progress, 23 passing, 0 blocked).
- Next action: Dispatch the isolated test-writer subagent to write `backend/tests/db/test_models_operation_condition.py` from the feature's behavior/verification/source_refs alone.

#### Checkpoint 51 · M1-MODEL-006 implemented and passed its gate

- Feature: `M1-MODEL-006`
- Status change: `in_progress` → `passing` (same session).
- Result (isolated TDD split per AGENTS.md):
  1. Test-writer subagent (isolated context: only `behavior`/`verification`/source_refs, forbidden from reading the db module, migrations, or sibling model tests) produced `backend/tests/db/test_models_operation_condition.py` — 6 offline structural + 9 live checks (parametrized over real PostgreSQL and MySQL, each on a unique fresh-DB upgraded to head, DB dropped in teardown), pinning the §5.11/§5.15 contract with microsecond-precision time round-trips. It reported only the file path.
  2. Controlling-session RED: `make test-db-contract TEST=models_operation_condition` → 24 failed. First run showed 6 offline + 9 PostgreSQL live failing on the missing tables (correct RED) but 9 MySQL live erroring at fixture setup on a test-writer `%`-escaping bug (`GRANT ... TO 'litemcp'@'%'` + aiomysql %-formatting → TypeError). Fixed that setup bug, re-ran: 24 failed, all on missing `service_condition`/`mcp_task` — the real RED, both dialects.
  3. Fresh implementer subagent (test file + behavior text only) built the `ServiceCondition`/`McpTask` models on the shared `Base`, the Alembic revision `m1_model_006_condition_task` (`down_revision = m1_model_005_artifact_build`), reporting 16/24 (the 8 remaining failures were a test-file parent-row setup defect).
- **Controller adjudications (applied by the controller, re-verified):**
  1. **Enum column type assertions relaxed to `(String, ENUM_CODE)`.** The test asserted `isinstance(col.type, String)` for service_condition.type/status and mcp_task.status; `ENUM_CODE` (a TypeDecorator) is not a `String` subclass, so the repo's ENUM_CODE convention would fail the assertion. Relaxed to `(String, ENUM_CODE)` — consistent with M1-MODEL-001..005.
  2. **MySQL `%`-escaping in the GRANT setup.** `_grant_mysql_app_user` built `GRANT ... TO 'litemcp'@'%'`; aiomysql %-formats the statement when a parameters tuple is passed (even empty), raising `TypeError: not enough arguments for format string`. Escaped the host's `%` as `%%`.
  3. **Parent-row helpers missing audit fields.** `_insert_toolset`/`_insert_tool`/`_insert_artifact` omitted the prior-feature tables' NOT NULL `updated_at`/`updated_by`/`row_version` (`updated_by` has no default → NOT NULL violation on both dialects; `updated_at`/`row_version` are auto-filled by the model's Python defaults). Added the three fields explicitly, matching the established contract helpers.
- Files changed: `backend/tests/db/test_models_operation_condition.py` (test-writer + controller adjudications), `backend/src/litemcp/db/models.py`, `backend/migrations/versions/m1_model_006_condition_task.py` (implementer), `feature_list.json` (status → passing + evidence), `progress.md`.
- Verification (controller re-ran every gate): `make test-db-contract TEST=models_operation_condition` → 24 passed (RED confirmed first; both live dialects, no skips). Full backend suite `pytest -q` → 181 passed (157 + 24), no regression. `ruff check src tests` clean. `mypy src` clean (10 source files). `alembic heads` → exactly one head `m1_model_006_condition_task`. `make ci-fast` → exit 0 (all seven legs incl. the 181-test backend suite + frontend). `node scripts/validate-feature-list.js` exits 0 (23 passing, 0 in_progress after transition).
- Evidence: recorded on `M1-MODEL-006` in `feature_list.json`.
- Commits: `3603aa9` `test(db): add operation/condition model contract tests (M1-MODEL-006)`; `7623d10` `feat(db): add ServiceCondition/McpTask models and m1_model_006_condition_task migration (M1-MODEL-006)`; state commit follows.
- Decision: The doc's loose `toolset_id/tool_id` notation on mcp_task is adjudicated as a single NOT-NULL `tool_id` FK→mcp_tool.id (pins the tool version at task creation). mcp_task deliberately carries only the MCP time fields `created_at`/`last_updated_at` (no §3.2 actor fields, no row_version) and NO UNIQUE constraints (task rows are append-only runtime records). Terminal-state/GC rules are application-layer. `make test-db-contract TEST=suite_name` gate reused unchanged.
- Next action: `M1-MODEL-007` (权限与 API Key 元数据模型, priority 109, depends on passing `M1-MODEL-001` + `M1-MODEL-002`) — the next model feature on top of the now-extended graph.

### Session 021 · 2026-08-10

#### Goal

- Implement `M1-MODEL-005` (Artifact 与 Build 模型) via the AGENTS.md isolated TDD workflow: test-writer and implementer dispatched as separate subagents with isolated context, RED/GREEN verified by the controlling session.

#### Checkpoint 48 · M1-MODEL-005 activated

- Feature: `M1-MODEL-005`
- Status change: `not_started` → `in_progress`.
- Result: Baseline confirmed. `backend/src/litemcp/db/models.py` holds `Base` + `User`/`Team`/`Service`/`ServiceConfigRevision`/`Toolset`/`McpTool`/`TeamMembership` (M1-MODEL-001..004) — no `ServiceArtifact`/`BuildRun` model. `backend/migrations/versions/` holds `m1_db_003_bootstrap_root` + `m1_model_001_user_team` + `m1_model_002_service` + `m1_model_003_config_revision` + `m1_model_004_toolset` (no artifact/build migration). `backend/tests/db/` has no `test_models_artifact_build.py`. Docker engine running (28.4.0). Contract from `01-data-model.md`: §5.5 `service_artifact` (unified record for code packages / service descriptors / dependency packages / run images: id, service_id FK→mcp_service, config_revision_id ID-nullable FK→`service_config_revision`, kind enum source_package/descriptor/build_bundle/container_image/build_log, storage_backend enum filesystem/s3/minio/registry, object_key varchar(1024), sha256 char(64), size_bytes bigint CHECK>=0, media_type varchar(128), format varchar(32), state enum staging/available/quarantined/gc_pending/deleted, scan_report JSON_DOC nullable, retain_until UTC_TS nullable, generic create fields, UNIQUE(storage_backend,object_key)); §5.6 `build_run` (id, service_id FK→mcp_service, config_revision_id FK→`service_config_revision` NOT NULL, source_artifact_id FK→service_artifact, strategy enum fastmcp [reserved descriptor/custom_adapter], parser_version varchar(64), base_image_digest varchar(255), dependency_digest char(64) nullable, status enum queued/building/validating/succeeded/failed/cancelled/superseded, output_artifact_id/log_artifact_id ID-nullable FK→service_artifact, discovered_descriptor JSON_DOC nullable, error_code varchar(64) nullable, error_summary varchar(2048) nullable, started_at/finished_at UTC_TS nullable, generic create fields, INDEX (service_id,status,created_at)). Both tables use the standard §3.2 audit set (no create-only carve-out); no §3.3 soft-delete. GC-safety (delete only gc_pending / past retain_until / unreferenced) is application-layer, not a DB trigger.
- Files changed: `feature_list.json` (status), `progress.md`.
- Verification: `node scripts/validate-feature-list.js` exits 0 (1 in_progress, 22 passing, 0 blocked).
- Next action: Dispatch the isolated test-writer subagent to write `backend/tests/db/test_models_artifact_build.py` from the feature's behavior/verification/source_refs alone.

#### Checkpoint 49 · M1-MODEL-005 implemented and passed its gate

- Feature: `M1-MODEL-005`
- Status change: `in_progress` → `passing` (same session).
- Result (isolated TDD split per AGENTS.md):
  1. Test-writer subagent (isolated context: only `behavior`/`verification`/source_refs, forbidden from reading the db module, migrations, or sibling model tests) produced `backend/tests/db/test_models_artifact_build.py` — 16 offline structural + 8 live checks (parametrized over real PostgreSQL and MySQL, each on a unique fresh-DB upgraded to head, DB dropped in teardown). It reported only the file path. (This test-writer already encoded the repo's cross-dialect conventions correctly: enum widths unpinned, CHECK violations expected as `(IntegrityError, OperationalError)`, FK target `mcp_service`.)
  2. Controlling-session RED: `make test-db-contract TEST=models_artifact_build` → 32 failed, all on `service_artifact`/`build_run` not registered on Base.metadata / KeyError — the missing models, not a typo or setup issue.
  3. Fresh implementer subagent (test file + behavior text only) built the `ServiceArtifact`/`BuildRun` models on the shared `Base`, the Alembic revision `m1_model_005_artifact_build` (`down_revision = m1_model_004_toolset`), and the cross-dialect `object_key` fix (below), reporting 32/32 GREEN.
- **Cross-dialect adjudication (controller-approved supporting change, recorded):** `object_key` is VARCHAR(1024) and participates in UNIQUE(storage_backend, object_key). Under MySQL utf8mb4 a 1024-char key is 4096 bytes — over InnoDB's 3072-byte index limit (errno 1071), so the initial migration failed all 8 MySQL live tests. Fix in the migration only: `sa.String(1024).with_variant(sa.String(1024, collation="latin1_bin"), "mysql")` — single-byte latin1 on MySQL preserves FULL-column uniqueness within the limit; PostgreSQL keeps plain VARCHAR(1024). The model metadata stays a plain String(1024) (offline length pin unaffected); the migration is the DDL source of truth (repo forbids empty autogenerate).
- Files changed: `backend/tests/db/test_models_artifact_build.py` (test-writer), `backend/src/litemcp/db/models.py`, `backend/migrations/versions/m1_model_005_artifact_build.py` (implementer), `feature_list.json` (status → passing + evidence), `progress.md`.
- Verification (controller re-ran every gate): `make test-db-contract TEST=models_artifact_build` → 32 passed (RED confirmed first; both live dialects, no skips). Full backend suite `pytest -q` → 157 passed (125 + 32), no regression. `ruff check src tests` clean. `mypy src` clean (10 source files). `alembic heads` → exactly one head `m1_model_005_artifact_build`. `make ci-fast` → exit 0 (all seven legs incl. the 157-test backend suite + frontend). `node scripts/validate-feature-list.js` exits 0 (22 passing, 0 in_progress after transition).
- Evidence: recorded on `M1-MODEL-005` in `feature_list.json`.
- Commits: `f325d3f` `test(db): add artifact/build model contract tests (M1-MODEL-005)`; `a1e20b2` `feat(db): add ServiceArtifact/BuildRun models and m1_model_005_artifact_build migration (M1-MODEL-005)`; state commit follows.
- Decision: `service_artifact` is the immutable object-record home and `build_run` the build-attempt ledger. The `strategy` CHECK allows fastmcp + reserved descriptor/custom_adapter (per §5.6 "预留"). GC-safety (delete only gc_pending / past retain_until / unreferenced) is application-layer — no DB trigger. The build_run artifact FKs are plain single-column FKs (no composite ownership, not required by the doc). `make test-db-contract TEST=suite_name` gate reused unchanged.
- Next action: `M1-MODEL-006` (Operation 与 Runtime Condition 模型, priority 108, depends on passing `M1-MODEL-002`) — the next model feature on top of the now-extended graph.

### Session 020 · 2026-08-10

#### Goal

- Implement `M1-MODEL-004` (Toolset 与 Tool 模型) via the AGENTS.md isolated TDD workflow: test-writer and implementer dispatched as separate subagents with isolated context, RED/GREEN verified by the controlling session.

#### Checkpoint 46 · M1-MODEL-004 activated

- Feature: `M1-MODEL-004`
- Status change: `not_started` → `in_progress`.
- Result: Baseline confirmed. `backend/src/litemcp/db/models.py` holds `Base` + `User`/`Team`/`Service`/`ServiceConfigRevision`/`TeamMembership` (M1-MODEL-001/002/003) — no `Toolset`/`McpTool` model. `backend/migrations/versions/` holds `m1_db_003_bootstrap_root` + `m1_model_001_user_team` + `m1_model_002_service` + `m1_model_003_config_revision` (no toolset migration). `backend/tests/db/` has no `test_models_toolset.py`. Docker engine running (28.4.0). Contract from `01-data-model.md`: §5.7 `toolset` (atomic publish unit: id, service_id FK→service, config_revision_id ID-nullable FK→`service_config_revision` [target now created by M1-MODEL-003 — real FK], version_no bigint UNIQUE(service_id,version_no), source_kind enum manual/fastmcp/descriptor/remote_mcp, source_digest char(64), mcp_protocol_version varchar(16), json_schema_dialect varchar(128) default 2020-12 URI, server_capabilities/server_info/validation_report JSON_DOC nullable, instructions LONG_TEXT nullable, state enum staging/validating/validated/active/rejected/retired, tool_count integer CHECK >=0, activated_at/retired_at UTC_TS nullable, generic create fields, UNIQUE(id,service_id) for the active-pointer cross-table ownership); §5.8 `mcp_tool` (lossless MCP Tool definitions: id, toolset_id FK→toolset ON DELETE CASCADE [staging cleanup], service_id redundant FK→service + composite FK (toolset_id,service_id)→toolset(id,service_id), name varchar(128) UNIQUE(toolset_id,name), title varchar(256) nullable, description LONG_TEXT nullable, input_schema/raw_definition JSON_DOC NOT NULL, output_schema/annotations/execution/icons/meta/http_binding JSON_DOC nullable, definition_digest char(64), source enum manual/synced, enabled BOOL default true, generic create fields — published tool not updated in place). Both tables use the standard §3.2 audit set (created_at/created_by/updated_at/updated_by/row_version) — §5.7/§5.8 have no create-only carve-out (unlike §5.3); no §3.3 soft-delete fields. mcp_tool composite FK requires toolset UNIQUE(id,service_id) which §5.7 declares.
- Files changed: `feature_list.json` (status), `progress.md`.
- Verification: `node scripts/validate-feature-list.js` exits 0 (1 in_progress, 21 passing, 0 blocked).
- Next action: Dispatch the isolated test-writer subagent to write `backend/tests/db/test_models_toolset.py` from the feature's behavior/verification/source_refs alone.

#### Checkpoint 47 · M1-MODEL-004 implemented and passed its gate

- Feature: `M1-MODEL-004`
- Status change: `in_progress` → `passing` (same session).
- Result (isolated TDD split per AGENTS.md):
  1. Test-writer subagent (isolated context: only `behavior`/`verification`/source_refs, forbidden from reading the db module, migrations, or sibling model tests) produced `backend/tests/db/test_models_toolset.py` — 4 offline structural + 4 live checks (parametrized over real PostgreSQL and MySQL, each on a unique fresh-DB upgraded to head, DB dropped in teardown), pinning the §5.7/§5.8 contract. It reported only the file path.
  2. Controlling-session RED: `make test-db-contract TEST=models_toolset` → 12 failed, all on `table 'toolset'/'mcp_tool' is not registered on Base.metadata` / `KeyError` — the missing models, not a typo or setup issue.
  3. Fresh implementer subagent (test file + behavior text only) built the `Toolset`/`McpTool` models on the shared `Base`, the Alembic revision `m1_model_004_toolset` (`down_revision = m1_model_003_config_revision`, creates `toolset` then `mcp_tool`), `json_schema_dialect` server_default (2020-12 URI) and `enabled` server_default (true). Reporting 9/12 (the 3 remaining failures were test-writer defects, not implementation).
- **Controller adjudication (three test-writer defects, applied by the controller and re-verified):**
  1. **FK target table name `service` → `mcp_service`.** The test asserted `_column_fk(..., "service_id", "service", "id")`, but the service table is `mcp_service` (M1-MODEL-002). Every authority agrees (the test's own docstring, the live tests' parent inserts, the implementation contract) the FK must target `mcp_service.id`. Fixed in both `test_toolset_constraints` and `test_mcp_tool_constraints`.
  2. **MySQL CHECK violations raise `OperationalError`, not `IntegrityError`.** CHECK violations surface as errno 3819 `OperationalError` under aiomysql/pymysql (PostgreSQL raises `IntegrityError`). The four CHECK-rejection blocks in `test_enum_and_range_check_constraints` now expect `(IntegrityError, OperationalError)` — the established `test_models_service.py` convention. FK/UNIQUE blocks unchanged (those raise `IntegrityError` on both dialects).
  3. **Enum column widths relaxed (not pinned).** `source_kind`/`state`/`source` exact varchar widths (32/16/16 in the test) conflict with the ENUM_CODE max+16 convention (26/26/22) — same adjudication as M1-MODEL-003. Replaced exact-width assertions with `_assert_enum` (`isinstance(type, (ENUM_CODE, String))` + nullability); non-enum String widths (digests 64, mcp_protocol_version 16, json_schema_dialect 128, name 128, title 256, audit 128) remain pinned. Plus 9 cosmetic ruff fixes (I001 import sort, UP017 `timezone.utc`→`UTC`, SIM102 nested if, BLE001 noqa).
- Files changed: `backend/tests/db/test_models_toolset.py` (test-writer + controller adjudications), `backend/src/litemcp/db/models.py`, `backend/migrations/versions/m1_model_004_toolset.py` (implementer), `feature_list.json` (status → passing + evidence), `progress.md`.
- Verification (controller re-ran every gate): `make test-db-contract TEST=models_toolset` → 12 passed (RED confirmed first; both live dialects, no skips). Full backend suite `pytest -q` → 125 passed (113 + 12), no regression. `ruff check src tests` clean. `mypy src` clean (10 source files). `alembic heads` → exactly one head `m1_model_004_toolset`. `make ci-fast` → exit 0 (all seven legs incl. the 125-test backend suite + frontend). `node scripts/validate-feature-list.js` exits 0 (21 passing, 0 in_progress after transition).
- Evidence: recorded on `M1-MODEL-004` in `feature_list.json`.
- Commits: `40ded60` `test(db): add toolset/tool model contract tests (M1-MODEL-004)`; `d50ac74` `feat(db): add Toolset/McpTool models and m1_model_004_toolset migration (M1-MODEL-004)`; state commit follows.
- Decision: `toolset.config_revision_id` is the first real FK landed to `service_config_revision` (M1-MODEL-003). Both the `toolset_id` FK and the composite FK `(toolset_id, service_id)→toolset(id, service_id)` carry `ondelete="CASCADE"` — the implementer verified empirically that a NO ACTION composite FK blocks the cascade on PostgreSQL (the single-column CASCADE deletes children, but the composite NO ACTION check then fails on the parent delete). `mcp_service.active_toolset_id` composite FK is now structurally enabled by `toolset` UNIQUE(id, service_id) but its enforcement remains deferred to the publication feature (§5.2 L165). The `make test-db-contract TEST=suite_name` gate is reused unchanged.
- Next action: `M1-MODEL-005` (Artifact 与 Build 模型, priority 107, depends on passing `M1-MODEL-002`) — the next model feature on top of the now-extended graph.

### Session 019 · 2026-08-10

#### Goal

- Implement `M1-MODEL-003` (配置 Revision 模型) via the AGENTS.md isolated TDD workflow: test-writer and implementer dispatched as separate subagents with isolated context, RED/GREEN verified by the controlling session.

#### Checkpoint 44 · M1-MODEL-003 activated

- Feature: `M1-MODEL-003`
- Status change: `not_started` → `in_progress`.
- Result: Baseline confirmed. `backend/src/litemcp/db/models.py` holds `Base` + `User`/`Team`/`TeamMembership`/`Service` (M1-MODEL-001/002) — no `ServiceConfigRevision` model. `backend/migrations/versions/` holds `m1_db_003_bootstrap_root` + `m1_model_001_user_team` + `m1_model_002_service` (no config-revision migration). `backend/tests/db/` has no `test_models_config_revision.py`. Docker engine running (28.4.0). Contract from `01-data-model.md` §5.3 `service_config_revision`: immutable content fields (`public_config` JSON_DOC, `secret_blob_id` ID-nullable [FK→`service_secret` deferred], `config_digest` char(64) SHA-256) plus lifecycle fields (`state` draft/validating/validated/active/rejected/superseded, `validation_report` JSON_DOC nullable, `activated_at`/`superseded_at` UTC_TS nullable); `service_id` FK→service RESTRICT; UNIQUE `(service_id,generation)`; UNIQUE `(id,service_id)` for the §5.2 L165 composite active-pointer FK; `config_kind` consistent with service type; `source_mode` enum (fastmcp_introspection/descriptor/manual/remote_sync); generic create fields only — NO updated fields (§5.3 L188). Known adjudication ahead: `secret_blob_id` FK target `service_secret` is a later table → pin as ID-nullable WITHOUT FK (M1-MODEL-002 active-pointer precedent); immutability is application-layer (later publication feature), so the DB contract pins absence of `updated_*` columns + UNIQUE(service_id,generation) rather than an UPDATE-rejecting trigger.
- Files changed: `feature_list.json` (status), `progress.md`.
- Verification: `node scripts/validate-feature-list.js` exits 0 (1 in_progress, 20 passing, 0 blocked).
- Next action: Dispatch the isolated test-writer subagent to write `backend/tests/db/test_models_config_revision.py` from the feature's behavior/verification/source_refs alone.

#### Checkpoint 45 · M1-MODEL-003 implemented and passed its gate

- Feature: `M1-MODEL-003`
- Status change: `in_progress` → `passing` (same session).
- Result (isolated TDD split per AGENTS.md):
  1. Test-writer subagent (isolated context: only `behavior`/`verification`/source_refs, forbidden from reading the db module, migrations, or sibling model tests) produced `backend/tests/db/test_models_config_revision.py` — 10 offline structural + 6 live checks (parametrized over real PostgreSQL and MySQL, each provisioning a unique empty database, running Alembic `upgrade head`, driving the table through an async engine, and dropping the DB in teardown). It reported only the file path.
  2. Controlling-session RED: `make test-db-contract TEST=models_config_revision` → 20 failed, all on `service_config_revision is not registered on Base.metadata` / `KeyError` — the missing ServiceConfigRevision model, not a typo or setup issue.
  3. Fresh implementer subagent (test file + behavior text only) built the `ServiceConfigRevision` model on the shared `Base` (`service_config_revision`: id, service_id FK RESTRICT, generation, schema_version, config_kind/public_config/secret_blob_id/source_descriptor/source_mode/config_digest/state/validation_report/activated_at/superseded_at, generic create fields created_at/created_by only — NO `updated_*`/`row_version` per §5.3 L188; UNIQUE (service_id,generation) + UNIQUE (id,service_id); CHECK config_kind/source_mode/state), the Alembic revision `m1_model_003_config_revision` (`down_revision = m1_model_002_service`), and the narrow `JSON_DOC(none_as_null=True)` supporting change to `types.py` (top-level None binds SQL NULL so NOT NULL JSON is DB-enforced), reporting 20/20 GREEN (full suite 113 passed).
- **Controller adjudication (four items, applied by the controller and re-verified):**
  1. **created_by type (test-writer pinned ID → uniform §3.2 String(128)).** The test-writer asserted `created_by` as ID kind; the uniform §3.2 audit convention shared by user/team/team_membership/mcp_service is String(128) (audit actor identity, not an entity FK). Aligned the test contract (kind id → str), the model, and the migration to String(128).
  2. **Enum column widths relaxed (not pinned).** The test-writer pinned exact varchar widths 24/32/16 for config_kind/source_mode/state; ENUM_CODE computes max-code-len+16 (24/36/26). Per M1-MODEL-001/002 precedent, enum-like columns' exact width is NOT pinned — only the CHECK enumeration content is. Relaxed the assertions to `isinstance(type, (ENUM_CODE, String))`; `config_digest` String(64) and all other non-enum lengths remain pinned.
  3. **Test helper iteration fix.** `table.indexes.values()` → `table.indexes` (SQLAlchemy 2.0.51 `Table.indexes` is a Set, no `.values()`), matching test_models_service.py's pattern; assertions unchanged.
  4. **`JSON_DOC.none_as_null=True` (passing-file narrow supporting change).** The NOT NULL JSON contract (`test_content_fields_are_not_null`) requires top-level Python None to bind as SQL NULL (default SQLAlchemy JSON binds None as JSON literal null, which satisfies NOT NULL). `JSON_DOC` now defaults `none_as_null=True`, forwarded through `load_dialect_impl` to JSONB/JSON. No existing test/code path relied on the old behavior (verified: test_types/test_models_user_team/test_models_service/test_migrations all green).
- Files changed: `backend/tests/db/test_models_config_revision.py` (test-writer + controller adjudications + implementer helper fix), `backend/src/litemcp/db/models.py`, `backend/migrations/versions/m1_model_003_config_revision.py` (implementer), `backend/src/litemcp/db/types.py` (controller-approved supporting change), `feature_list.json` (status → passing + evidence), `progress.md`.
- Verification (controller re-ran every gate after adjudication): `make test-db-contract TEST=models_config_revision` → 20 passed (RED confirmed first; both live dialects, no skips). Full backend suite `pytest -q` → 113 passed (93 + 20), no regression. `ruff check src tests` clean. `mypy src` clean (10 source files). `alembic heads` → exactly one head `m1_model_003_config_revision`. `make ci-fast` → exit 0 (all seven legs incl. the 113-test backend suite + frontend). `node scripts/validate-feature-list.js` exits 0 (20 passing, 0 in_progress after transition).
- Evidence: recorded on `M1-MODEL-003` in `feature_list.json`.
- Commits: `cef84e2` `test(db): add config revision model contract tests (M1-MODEL-003)`; `e23b2ec` `feat(db): add ServiceConfigRevision model and m1_model_003_config_revision migration (M1-MODEL-003)`; state commit follows.
- Decision: `service_config_revision` is the immutable revision home for §5.3. The two active-version pointers on `mcp_service` (M1-MODEL-002) remain FK-less — the composite FK wiring `(active_config_revision_id, id)` → `(id, service_id)` is now structurally enabled by UNIQUE(id,service_id) but its enforcement is deferred to the publication feature (§5.2 L165). `secret_blob_id` FK → `service_secret.id` remains deferred (service_secret not created in M1). `JSON_DOC(none_as_null=True)` is now the shared JSON-column bind semantics for all subsequent models. The `make test-db-contract TEST=suite_name` gate is reused unchanged for M1-MODEL-004+ model contracts.
- Next action: `M1-MODEL-004` (Toolset 与 Tool 模型, priority 106, depends on passing `M1-MODEL-002`) — the next model feature on top of the now-extended graph.

### Session 018 · 2026-08-10

#### Goal

- Implement `M1-MODEL-002` (服务模型) via the AGENTS.md isolated TDD workflow: test-writer and implementer dispatched as separate subagents with isolated context, RED/GREEN verified by the controlling session.

#### Checkpoint 42 · M1-MODEL-002 activated

- Feature: `M1-MODEL-002`
- Status change: `not_started` → `in_progress`.
- Result: Baseline confirmed. `backend/src/litemcp/db/models.py` holds the shared `Base` plus `User`/`Team`/`TeamMembership` (M1-MODEL-001) — no `Service` model. `backend/migrations/versions/` holds only `m1_db_003_bootstrap_root` + `m1_model_001_user_team` (no service migration). `make test-db-contract TEST=suite_name` gate already exists (runs `tests/db/test_$(TEST).py` after starting both compose DBs) so `TEST=models_service` needs only the test file. Docker engine running (28.4.0). Service contract from `01-data-model.md` §5.2: `mcp_service` holds stable identity + desired state + publish pointers + runtime summary (id PK; namespace_key varchar(64) default `default`; team_id FK→team RESTRICT; type varchar(24) CHECK http_api/stdio/mcp_http; name/name_normalized varchar(128); uniqueness_scope varchar(64) default `LIVE`; tags JSON_DOC default `[]`; description LONG_TEXT nullable; icon_object_key varchar(512) nullable; desired_status varchar(16) CHECK enabled/disabled; generation bigint default 1; observed_generation bigint default 0; runtime_status varchar(24) CHECK pending/ready/degraded/unhealthy/failed; active_config_revision_id + active_toolset_id ID nullable [FKs to `service_config_revision`/`toolset` deferred — those tables are later model features]; agent_auth_mode varchar(24) CHECK api_key/none/oauth2; rate_limit_qps decimal nullable >0; rate_limit_burst integer nullable >=1; queue_max_depth/queue_timeout_ms/stdio_instance_max/stdio_concurrency_per_instance integer nullable stdio-only; audit fields per §3.2; soft-delete per §3.3 incl. deleted_at/deleted_by). Constraints: UNIQUE `(namespace_key, name_normalized, uniqueness_scope)`; INDEX `(namespace_key, desired_status, type)`, `(team_id, desired_status)`, `(created_by, deleted_at)`; CHECK `observed_generation <= generation`; CHECK stdio-only columns NULL for non-stdio; no physical delete — disable + uniqueness_scope change (§5.2 L166).
- Files changed: `feature_list.json` (status), `progress.md`.
- Verification: `node scripts/validate-feature-list.js` exits 0 (1 in_progress, 19 passing, 0 blocked).
- Next action: Dispatch the isolated test-writer subagent to write `backend/tests/db/test_models_service.py` from the feature's behavior/verification/source_refs alone.

#### Checkpoint 43 · M1-MODEL-002 implemented and passed its gate

- Feature: `M1-MODEL-002`
- Status change: `in_progress` → `passing` (same session).
- Result (isolated TDD split per AGENTS.md):
  1. Test-writer subagent (isolated context: only `behavior`/`verification`/source_refs, forbidden from reading the db module or the sibling model test) produced `backend/tests/db/test_models_service.py` — 20 tests: 8 offline structural (mcp_service registered on `Base.metadata`, all 30 columns with portable kinds, UNIQUE `(namespace_key, name_normalized, uniqueness_scope)`, three indexes, portable CHECKs, team FK, audit/soft-delete fields) + 12 live checks parametrized over REAL PostgreSQL and MySQL, each provisioning a uniquely-named empty database, running Alembic `upgrade head`, asserting CRUD/UNIQUE/CHECK/FK behavior, and dropping the DB in teardown. It reported only the file path.
  2. Controlling-session RED: 20 failed, all on `AssertionError: mcp_service table is not registered on litemcp.db.models.Base.metadata` — the missing Service model, not a typo or setup issue.
  3. Fresh implementer subagent (test file + behavior text only) built the `Service` model on the shared `Base` (`mcp_service`: id, namespace_key, team_id FK RESTRICT, type/desired_status/runtime_status/agent_auth_mode ENUM_CODE CHECKs, name/name_normalized, uniqueness_scope, tags JSON_DOC, description LONG_TEXT, icon_object_key, generation/observed_generation BigInteger, the two nullable ID-typed active version pointers WITHOUT FK, rate_limit_qps Numeric, four stdio-only Integer columns, §3.2 audit + §3.3 soft-delete fields, UNIQUE + 3 indexes), the Alembic revision `m1_model_002_service` (`down_revision = m1_model_001_user_team`), reporting 20/20 GREEN. Full suite 93 passed (73 + 20).
- **Controller adjudication (three items, applied by the controller and re-verified):**
  1. **Audit/soft-delete identity type.** The test-writer pinned `created_by`/`updated_by`/`deleted_by` as `ID` (UUID) kind, but the uniform §3.2 audit convention already established by `user`/`team`/`team_membership` is `String(128)` (audit actor identity, not an entity FK). Aligned the test contract (kind `id` → `str` for those three columns), the `Service` model, and the migration to `String(128)` for data-layer consistency.
  2. **stdio-only CHECK strengthened to all four columns.** The implementer's CHECK only enforced `queue_max_depth IS NULL` for non-stdio, but `01-data-model.md` §5.2 L163 requires `queue_max_depth`/`queue_timeout_ms`/`stdio_instance_max`/`stdio_concurrency_per_instance` ALL NULL for non-stdio. Strengthened the CHECK in model + migration to `(type = 'stdio' OR (queue_max_depth IS NULL AND queue_timeout_ms IS NULL AND stdio_instance_max IS NULL AND stdio_concurrency_per_instance IS NULL))` AND tightened the offline test assertion to require all four column names in the CHECK text, so a regression to the weaker constraint is caught.
  3. **Cosmetic ruff in the test-writer file** (applied by controller, assertions unchanged): RUF059 unused `base_url` unpack → `_base_url`; FURB157 `Decimal("1")` → `Decimal(1)`.
- Files changed: `backend/tests/db/test_models_service.py` (test-writer + controller adjudications), `backend/src/litemcp/db/models.py` (implementer + controller audit/stdio fixes), `backend/migrations/versions/m1_model_002_service.py` (implementer + controller fixes), `feature_list.json` (status → passing + evidence), `progress.md`.
- Verification (controller re-ran every gate after adjudication): `make test-db-contract TEST=models_service` → 20 passed (RED confirmed first; both live dialects, no skips). Full backend suite `pytest -q` → 93 passed (73 + 20), no regression. `ruff check src tests` clean. `mypy src` clean (10 source files). `alembic heads` → exactly one head `m1_model_002_service`. `make ci-fast` → exit 0 (all seven legs incl. the 93-test backend suite + frontend). `node scripts/validate-feature-list.js` exits 0 (20 passing, 0 in_progress after transition).
- Evidence: recorded on `M1-MODEL-002` in `feature_list.json`.
- Commits: `3a8e24e` `test(db): add service model contract tests (M1-MODEL-002)`; `6005345` `feat(db): add Service model and m1_model_002_service migration (M1-MODEL-002)`; state commit follows.
- Decision: The two active version pointers (`active_config_revision_id`/`active_toolset_id`) are deliberately pinned as present + nullable + ID-typed WITHOUT FK — their enforcement is a later feature that creates `service_config_revision`/`toolset` and wires the composite FK per §5.2 L165 (recorded so no later feature silently drops or re-types them). The `make test-db-contract TEST=suite_name` gate is reused unchanged for M1-MODEL-003+ model contracts. The `Service` model appends to the shared `Base`; the migration extends the graph from `m1_model_001_user_team`.
- Next action: `M1-MODEL-003` (优先级 105, depends on passing `M1-MODEL-002`) — the next model feature on top of the now-extended graph.

### Session 017 · 2026-08-10

#### Goal

- Implement `M1-MODEL-001` (用户与团队模型) via the AGENTS.md isolated TDD workflow: test-writer and implementer dispatched as separate subagents with isolated context, RED/GREEN verified by the controlling session.

#### Checkpoint 40 · M1-MODEL-001 activated

- Feature: `M1-MODEL-001`
- Status change: `not_started` → `in_progress`.
- Result: Baseline confirmed. `backend/src/litemcp/db/` holds only `__init__.py` + `session.py` (AsyncSessionFactory) + `types.py` (six cross-dialect TypeDecorators); no model module exists anywhere. `backend/migrations/versions/` holds only the hand-written base root `m1_db_003_bootstrap_root` (no entity migrations). `backend/tests/db/` has test_session/test_types/test_migrations — no model contract test. Declared verification `make test-db-contract TEST=models_user_team` has no test file and no Makefile target yet (existing db gates: test-db-types, test-migrations). Model contract reviewed from `01-data-model.md`: §5.1 `user` (id ID PK, username varchar(128), username_normalized varchar(128) UNIQUE, password_hash varchar(255), role varchar(16) CHECK admin/user, status varchar(16) CHECK active/disabled/locked, password_changed_at UTC_TS, last_login_at UTC_TS nullable, failed_login_count integer default 0 >= 0, failed_login_window_started_at UTC_TS nullable, locked_until UTC_TS nullable, generic audit fields — no physical delete, disable instead); §5.16 `team` (id ID PK, key varchar(64), key_normalized varchar(64) NFKC+trim+casefold UNIQUE, name varchar(128), description LONG_TEXT nullable, status varchar(16) CHECK active/archived, audit fields incl. row_version — no physical delete, archive instead, default team key=default created at deployment init); §5.17 `team_membership` (id ID PK, team_id ID FK→team RESTRICT, user_id ID FK→user, team_role varchar(16) CHECK admin/member, audit fields, UNIQUE(team_id,user_id)). Generic audit fields per §3.2: created_at/created_by/updated_at/updated_by/row_version on all mutable business tables; constraints (FK/UNIQUE/CHECK) must live in the DB layer, not Pydantic (§3.2 L63).
- Files changed: `feature_list.json` (status), `progress.md`.
- Verification: `node scripts/validate-feature-list.js` exits 0 (1 in_progress, 18 passing, 0 blocked).
- Next action: Dispatch the isolated test-writer subagent to write `backend/tests/db/test_models_user_team.py` from the feature's behavior/verification/source_refs alone.

#### Checkpoint 41 · M1-MODEL-001 implemented and passed its gate

- Feature: `M1-MODEL-001`
- Status change: `in_progress` → `passing` (same session).
- Result (isolated TDD split per AGENTS.md):
  1. Test-writer subagent (isolated context: only `behavior`/`verification`/source_refs, forbidden from reading the db module) produced `backend/tests/db/test_models_user_team.py` — 13 cases: 1 offline structural contract (Base.metadata tables, columns/lengths/`litemcp.db.types` types, UNIQUE on username_normalized/key_normalized/(team_id,user_id), portable CHECK enumerating every allowed code, FK team.id/user.id, no deleted_at/uniqueness_scope) + 12 live checks parametrized over REAL PostgreSQL and MySQL, each provisioning a uniquely-named empty database, running Alembic `upgrade head` in a worker thread, and driving the ORM through an `AsyncSessionFactory` bound to the dedicated URL (DB dropped in teardown). It reported only the file path.
  2. Controlling-session RED: `ModuleNotFoundError: No module named 'litemcp.db.models'` — the missing model module, not a typo or setup issue.
  3. Fresh implementer subagent (test file + behavior text only) built `backend/src/litemcp/db/models.py` (`Base`/`User`/`Team`/`TeamMembership`), the Alembic revision `m1_model_001_user_team` (down_revision = bootstrap root, creates exactly the three tables with matching UNIQUE/CHECK/FK), and the `make test-db-contract TEST=suite_name` gate (mirrors test-db-types/test-migrations), reporting 13/13 GREEN.
- **Controller adjudication of two supporting changes:**
  1. `session.py` gained `expire_on_commit=False` (a M1-DB-001 passing-file narrow change): the contract test commits objects, releases the session, then reads the committed objects' attributes (`got_team.id == team.id`); with the SQLAlchemy default `expire_on_commit=True` the detached objects' attributes are expired and raise DetachedInstanceError. `expire_on_commit=False` keeps committed values readable without a round-trip — a common production session setting that does not weaken M1-DB-001's independent-session / reliable-release contract (verified: full suite 73 passed, session concurrency/release tests still green).
  2. `TeamMembership` declares `relationship()` to `team`/`user` so the unit of work flushes the referenced user/team before the membership row (FK dependency), avoiding cross-dialect FK violations on flush ordering.
- **Cross-environment regression caught by the M0-CMD-001 gate and fixed by the controller:** the implementer's new `make help` line contained a literal `(TEST=<module>)`. GNU make hands recipe lines to the recipe shell unchanged except for `$(...)` expansion; cmd.exe parses `<` as an INPUT REDIRECTION operator, so `echo ... (TEST=<module>)` tried to read a file named `module>` and exited non-zero — `make help` failed under the PowerShell/cmd branch while passing under Git Bash/sh (where `$(Q)` wraps the line in single quotes). Reworded the help text to `(TEST=suite_name)`; make-help gate re-verified 7/7 in BOTH branches. (The `validate-make-help.test.js` gate is the honesty net that caught this before it could ship.)
- Files changed: `backend/tests/db/test_models_user_team.py` (test-writer), `backend/src/litemcp/db/models.py`, `backend/src/litemcp/db/session.py`, `backend/migrations/versions/m1_model_001_user_team.py`, `Makefile` (implementer + controller help fix), `feature_list.json` (status → passing + evidence), `progress.md`.
- Verification (controller re-ran every gate): `make test-db-contract TEST=models_user_team` → 13 passed (RED confirmed first; both live dialects, no skips). Full backend suite `pytest -q` → 73 passed (60 + 13), no regression. `ruff check src tests` clean; `mypy src` clean (10 source files); `alembic heads` → exactly one head `m1_model_001_user_team`. `node --test scripts/validate-make-help.test.js` → 7/7 in both cmd and sh branches. `make ci-fast` → exit 0 (all seven legs incl. the 73-test backend suite + frontend). `node scripts/validate-feature-list.js` exits 0 (18 passing, 1 in_progress before transition).
- Evidence: recorded on `M1-MODEL-001` in `feature_list.json`.
- Commits: `6b46443` `test(db): add user/team/membership model contract tests (M1-MODEL-001)`; `0505ea3` `feat(db): add user/team/membership models, migration, test-db-contract gate (M1-MODEL-001)`; state commit follows.
- Decision: The `make test-db-contract TEST=suite_name` gate is the generic model-contract gate that M1-MODEL-002 through M1-MODEL-008 will reuse with their own `TEST=` selectors. The entity migration now extends the graph from `m1_db_003_bootstrap_root`; M1-DB-003's fresh-upgrade tests still pass (they only assert alembic_version reaches the single head). The `Base` in `litemcp.db.models` is the shared declarative base all subsequent model features append to.
- Next action: `M1-MODEL-002` (服务模型 `make test-db-contract TEST=models_service`, priority 104, depends on passing `M1-DB-002` + `M1-DB-003`) — the first model feature on top of the now-shared `litemcp.db.models.Base`.

### Session 001 · 2026-08-09

#### Goal

- Review the existing LiteMCP implementation and target architecture.
- Establish a fine-grained machine-readable feature list.
- Establish a durable progress and verification protocol for future conversations.

#### Checkpoint 1 · Project baseline reviewed

- Feature: `M0-HARNESS-001`
- Result: Confirmed that the repository is an early scaffold while the target system is specified by the M0–M8 architecture documents.
- Evidence: CodeGraph indexed 21 source files; the backend currently exposes only `/livez` and `/readyz`; the frontend is still the HeroUI/Vite starter experience.
- Files changed: None.
- Decision: Planned architecture must not be reported as implemented behavior.

#### Checkpoint 2 · Harness reference incorporated

- Feature: `M0-HARNESS-001`
- Result: Adopted the repository-as-system-of-record, single-active-feature, pass-gate, evidence, and clean-handoff principles from the supplied Harness Engineering reference.
- Evidence: Reviewed its `feature_list.json`, progress log, `AGENTS.md`, pass-gate policy, session handoff, and clean-state checklist templates.
- Files changed: None.
- Decision: Keep the primary harness limited to `feature_list.json`, `progress.md`, and project-level `AGENTS.md` rather than introducing redundant handoff documents.

#### Checkpoint 3 · Initial feature inventory created

- Feature: `M0-HARNESS-001`
- Result: Created 141 session-sized features across M0–M8 with explicit behavior, dependencies, verification commands, expected results, evidence slots, and source references.
- Status change: `not_started` → `in_progress`.
- Files changed: `feature_list.json`.
- Verification: Pending structural and dependency validation.
- Known risk: Planned verification commands reference test and Makefile targets that will be implemented by their corresponding features.

#### Checkpoint 4 · Initial feature-list validation failed

- Feature: `M0-HARNESS-001`
- Result: JSON parsing, feature count, milestone count, unique IDs, required fields, status counts, and dependency references were checked.
- Verification: A Node-based structural validation command ran and exited with status 1.
- Evidence: 141 features, 9 milestones, one active feature, and one invalid dependency: `M8-SUPPLY-001` referenced missing `M6-BUILD-005`.
- Files changed: None.
- Decision: Keep the feature `in_progress`; a failed verification cannot produce `passing`.
- Next action: Correct the dependency to the existing artifact registration feature and rerun the full structural validation.

#### Checkpoint 5 · Feature inventory passed its gate

- Feature: `M0-HARNESS-001`
- Result: Corrected `M8-SUPPLY-001` to depend on the existing `M6-BUILD-004` artifact-registration feature.
- Status change: `in_progress` → `passing`.
- Verification: Re-ran the structural validator successfully.
- Evidence: 141 features, 9 milestones, unique IDs, required fields, allowed statuses, valid dependency references, no dependency cycles, and exactly one active feature before transition.
- Files changed: `feature_list.json`.
- Next action: Activated `M0-HARNESS-002` to verify the cross-conversation progress log.

#### Checkpoint 6 · Progress-log feature activated

- Feature: `M0-HARNESS-002`
- Status change: `not_started` → `in_progress`.
- Result: The progress log now contains the durable protocol, current verified snapshot, session goal, chronological checkpoints, unresolved work, and next-step contract.
- Files changed: `progress.md`.
- Verification: Pending the feature's declared focused check and content checks.

#### Checkpoint 7 · Progress log passed its gate

- Feature: `M0-HARNESS-002`
- Status change: `in_progress` → `passing`.
- Verification: Checked the required current-state, session-log, checkpoint, unresolved-work, and next-step sections.
- Evidence: Validation found no missing sections and counted one session with six checkpoints before this entry.
- Files changed: `feature_list.json`, `progress.md`.
- Next action: Activated `M0-HARNESS-003` to verify the project-level Agent working agreement.

#### Checkpoint 8 · Agent protocol feature activated

- Feature: `M0-HARNESS-003`
- Status change: `not_started` → `in_progress`.
- Result: Added startup workflow, single-active-feature rule, evidence-gated passing, immediate checkpoint rules, CodeGraph guidance, session exit procedure, and clean-state checklist.
- Files changed: `AGENTS.md`.
- Verification: Pending focused content validation.

#### Checkpoint 9 · Agent protocol passed its gate

- Feature: `M0-HARNESS-003`
- Status change: `in_progress` → `passing`.
- Verification: Checked nine required rules covering startup, source of truth, single-active execution, passing evidence, checkpoints, CodeGraph, session exit, and clean state.
- Evidence: All nine rule patterns were present in `AGENTS.md`.
- Files changed: `feature_list.json`, `AGENTS.md`, `progress.md`.
- Next action: Run a final cross-file harness validation, then leave no active feature.

#### Checkpoint 10 · Harness foundation completed

- Features: `M0-HARNESS-001`, `M0-HARNESS-002`, `M0-HARNESS-003`.
- Result: The feature inventory, progress log, and Agent working agreement are mutually consistent and ready for future sessions.
- Verification: Final JSON/state/dependency/cycle validator, required Markdown section checks, required Agent rule checks, and `git diff --check` all passed.
- Evidence: 141 features across 9 milestones; 3 passing with evidence; 138 not started; 0 active; 0 structural errors; no whitespace errors.
- Files changed: `feature_list.json`, `progress.md`, `AGENTS.md`.
- Next action: Begin `M0-ENV-001` only when implementation of the next product feature is requested.

#### Broken Or Unverified

- Product implementation features remain intentionally `not_started` until their declared verification gates exist and pass.
- Most planned verification commands refer to tests and Makefile targets that later M0–M8 features will create.
- `M2-SCOPE-001` through `M8-SCOPE-001` are placeholders (see Checkpoint 11) — they must be decomposed into session-sized features before any implementation in those milestones begins.

#### Next Best Step

- Feature: `M0-ENV-001`
- Why it is next: It is the highest-priority unfinished feature after the Harness foundation and establishes typed backend configuration needed by later infrastructure.
- What counts as passing: The backend loads typed environment configuration, applies documented safe defaults, and fails fast for missing required values under its declared focused tests.
- What must not change: Existing `/livez` behavior and the evidence-gated feature state protocol.

### Session 002 · 2026-08-09

#### Goal

- Recalibrate the harness against `learn-harness-engineering` reference material after a user-requested review, addressing four gaps: no committed structural validator, no enforcement gate, 141 features planned upfront with M2+ granularity likely to go stale, and security features whose verification did not force negative-case coverage.

#### Checkpoint 11 · Harness recalibration completed

- Feature: `M0-HARNESS-004`
- Status change: `not_started` → `passing` (created and passed in the same session; see evidence below).
- Result:
  1. Added `scripts/validate-feature-list.js` — a reusable, dependency-free structural validator (unique IDs, required fields, allowed statuses, dependency existence, dependency cycles, single active feature, `passing` requires non-empty evidence, no `passing` feature depending on a non-`passing` feature).
  2. Added `.githooks/pre-commit`, which runs the validator whenever `feature_list.json` is staged; `AGENTS.md` startup workflow now instructs enabling it via `git config core.hooksPath .githooks` (not run automatically — local git config changes are left to the user/session that needs them).
  3. Added explicit negative-case verification entries to `M1-SEC-001`, `M1-SEC-002`, `M1-SEC-003` (key rotation failure, plaintext-never-persisted canaries, redaction under uncaught exceptions/exception chains).
  4. Collapsed the 95 pre-written M2–M8 features into 7 per-milestone `*-SCOPE-001` placeholders (141 → 38 total features). Each placeholder's `behavior` field preserves every original feature's id/title/behavior text verbatim, `notes` lists the original ids, and `verification` requires decomposition back into session-sized features (with real automated commands) before any implementation in that milestone and forbids marking the placeholder itself `passing`. M0/M1 were left untouched since they are the near-term, actionable work.
- Files changed: `scripts/validate-feature-list.js` (new), `.githooks/pre-commit` (new), `feature_list.json`, `AGENTS.md`, `progress.md`.
- Verification: `node scripts/validate-feature-list.js` run after every edit in this session; final run reports 38 features / 9 milestones, 4 passing, 0 in_progress, 0 blocked, 0 structural errors.
- Evidence: recorded on `M0-HARNESS-004` in `feature_list.json`.
- Decision: Original fine-grained M2–M8 rows are not preserved as separate files — they live in git history prior to this commit and verbatim inside each placeholder's `behavior` text, so no information was lost, only deferred.
- Next action: Continue with `M0-ENV-001` (unchanged). When M2 work eventually starts, the first step is decomposing `M2-SCOPE-001`, not implementing auth code directly.

#### Checkpoint 12 · Redundancy cleanup in feature_list.json

- Feature: `M0-HARNESS-004` (same feature, follow-up within the same session).
- Result: User asked whether the feature list had redundant parts; found and fixed three: (1) `M1-SEC-001/002/003` stated the same negative-case list twice, once in `behavior` and once restated in the new manual `verification` entry — the manual entries now point back to `behavior` instead of duplicating it; (2) the 7 `*-SCOPE-001` placeholders repeated the collapsed feature id list in both `behavior` and `notes` — `notes` now just points at `behavior`; (3) the global `rules.feature_should_fit_one_session: true` literally contradicted the intentionally coarse `*-SCOPE-001` placeholders, so an explicit `rule_exceptions` block was added rather than leaving the contradiction implicit.
- Files changed: `feature_list.json`.
- Verification: `node scripts/validate-feature-list.js` — 38 features, 4 passing, 0 structural errors.
- Known residual: `M0-HARNESS-001`/`002`/`003` evidence entries still say "141 features" / "1 session, 6 checkpoints" — left as-is intentionally, since evidence is a timestamped record of what was true when that feature passed, not a live count.

### Session 003 · 2026-08-09

#### Goal

- Baseline audit for `M0-ENV-001` per the staged audit plan (Phase A): hooks, config contract, test infrastructure gap, and Windows-equivalent verification command.

#### Checkpoint 13 · Phase A baseline audit completed

- Feature: `M0-ENV-001` (baseline audit only, feature remains `not_started`)
- Result: All four Phase A items closed:
  1. `git config core.hooksPath .githooks` set once in this clone; pre-commit hook confirmed to run `node scripts/validate-feature-list.js` when `feature_list.json` is staged. Note: the hook requires `node` on PATH of the committing environment (Windows Git for Windows has it; WSL bash in this session does not).
  2. Config contract reviewed in `08-implementation-plan.md`, `00-overview.md`, `09-verification.md`: config comes from protected environment (twelve-factor); M0 exit standard requires **startup refusal** for sample/short secrets, wide Origin, wrong trusted proxy, or debug in production, plus zero canary-secret log leakage. `00-overview.md` mandates the file at `backend/src/litemcp/core/config.py`; tests/ tree in `00-overview.md` shows `unit/integration/dialects` subdirectories while the feature's declared verification points at `tests/core/test_config.py` — recorded as a decision point, do not silently change the feature's verification.
  3. Test infrastructure gap confirmed: no `tests/` dir, no `conftest.py`, no `[tool.pytest.ini_options]` (no `asyncio_mode` configured) in `pyproject.toml`; `pydantic-settings` already in dependencies; `backend/src/litemcp/__init__.py` still the scaffold `Hello from litemcp!` main.
  4. Windows-equivalent verification confirmed: `backend/.venv/Scripts/python.exe` (Python 3.13.2) with `pydantic-settings` and `pytest 9.1.1` installed; `uv` is not available in WSL bash. Equivalent command: `cd backend && .venv/Scripts/python.exe -m pytest tests/core/test_config.py`.
- Files changed: `.git/config` (hooksPath, local), `progress.md`.
- Verification: `node scripts/validate-feature-list.js` (via Windows node) still exits 0 — 38 features, 4 passing, 0 in_progress, 0 blocked.
- Evidence: see result above.
- Decision: Phase B (implementation) may start on `M0-ENV-001`; pytest asyncio config and `tests/` scaffolding are narrow supporting changes to be recorded when introduced. The tests directory layout decision (feature-declared `tests/core/test_config.py` vs doc `tests/unit/...`) will be resolved in favor of the feature's declared verification path.
- Next action: Implement `M0-ENV-001` per the audit plan Phase B: `core/config.py` typed settings + fail-fast, `tests/core/test_config.py` with defaults/override/missing-required negative cases, keep `/livez` green.

#### Checkpoint 14 · M0-ENV-001 implemented and passed its gate

- Feature: `M0-ENV-001`
- Status change: `not_started` → `in_progress` → `passing` (same session).
- Result:
  1. `backend/src/litemcp/core/config.py` — typed `Settings(BaseSettings)` with `LITEMCP_` env prefix, `.env` support; required `database_url`/`redis_url`/`encryption_keys` fail fast with `ValidationError`; documented safe defaults (env=dev, debug=false, gateway_enabled=false, storage local); comma-separated list parsing via `NoDecode` + before-validators; prod refusal of debug, wildcard/blank Origin, sample or short Fernet keys (M0 exit standard from `08-implementation-plan.md`); cached `get_settings()` singleton.
  2. `backend/tests/core/test_config.py` — 17 tests: safe defaults, SecretStr typing, env overrides (scalars/comma lists/storage), missing-required negative cases (each required var), blank-key rejection, prod negative cases (debug, `*`, blank origin, sample key, short key), explicit prod origin allowed, cached singleton.
  3. `backend/pyproject.toml` — added `[tool.pytest.ini_options]` (pythonpath=[src], testpaths=[tests], asyncio_mode=strict) as the narrow test-infrastructure support change; no dependency changes (pydantic-settings already declared).
  4. Two implementation iterations recorded: list env parsing needed `NoDecode` + before-validators (first run 16 failed); prod+debug scalar test was self-contradictory and the nested `storage` model used non-intuitive double-underscore env names — flattened `storage_backend`/`storage_path` (first run 4 failed).
- Files changed: `backend/src/litemcp/core/config.py` (new), `backend/tests/core/test_config.py` (new), `backend/pyproject.toml`, `feature_list.json`, `progress.md`.
- Verification: `cd backend && .venv/Scripts/python.exe -m pytest tests/core/test_config.py -v` → 17 passed; `ruff check` clean (3 auto-fixes); `mypy src/litemcp/core/config.py` clean (1 `# type: ignore[call-arg]` on `Settings()` documented); app import regression shows `/livez`/`/readyz` intact; `node scripts/validate-feature-list.js` exits 0 (5 passing, 0 in_progress).
- Evidence: recorded on `M0-ENV-001` in `feature_list.json`.
- Decision: Production-refusal checks live in the config layer per the M0 exit standard; `.env.example` coverage is deferred to `M0-ENV-002` (next).
- Next action: Start `M0-ENV-002` (.env.example covering database/Redis/keys/storage/gateway with no real secrets, verified by `make validate-env-example`).

### Session 004 · 2026-08-09

#### Goal

- Implement `M0-ENV-002`: root `.env.example` covering all five config groups with no real secrets, verified by `make validate-env-example` (declared command required a root Makefile, which did not exist).

#### Checkpoint 15 · M0-ENV-002 implemented and passed its gate

- Feature: `M0-ENV-002`
- Status change: `not_started` → `passing` (same session).
- Result:
  1. `.env.example` (repo root, per `00-overview.md` layout) documents all five config groups — database, Redis, encryption keys, storage, gateway — with placeholder values and generation/usage comments; no real secrets.
  2. `scripts/validate-env-example.js` — dependency-free node gate (mirrors `validate-feature-list.js` style) checking required-key coverage and rejecting secret-shaped values (Fernet key, AWS key, GitHub PAT, Slack/Stripe/OpenAI/Google tokens, PEM private keys).
  3. `scripts/validate-env-example.test.js` — 13 tests via `node:test`: complete example passes, comments/blank lines ignored, each missing required group reported, blank required value reported, and each real-secret pattern rejected; placeholder words (e.g. `change-me-...`) not flagged.
  4. Root `Makefile` with the single `validate-env-example` target — the narrow supporting change the user confirmed in-session to satisfy the declared `make` verification (full Makefile command set stays with `M0-CMD-001`).
- Files changed: `.env.example` (new), `Makefile` (new), `scripts/validate-env-example.js` (new), `scripts/validate-env-example.test.js` (new), `feature_list.json`, `progress.md`.
- Verification: TDD — wrote the failing test first (module missing), then implemented until 13/13 passed. `make validate-env-example` exits 0. Manual negative injection of a real Fernet key into `.env.example` made the gate exit non-zero with the expected error, then restored clean. Smoke test copying `.env.example` to `backend/.env` loaded via pydantic `Settings()` succeeded (app_name=litemcp, environment=dev, debug=false, gateway=false, storage=local, ./data/storage, 1 encryption key); `backend/.env` removed after check. `node scripts/validate-feature-list.js` exits 0 (6 passing, 0 in_progress).
- Evidence: recorded on `M0-ENV-002` in `feature_list.json`.
- Decision: The declared verification command `make validate-env-example` is satisfied by the new root Makefile; `make` (GNU Make 4.4.1 via chocolatey) and `node` are both available on this Windows environment.
- Next action: Start `M0-CMD-001` (root Makefile unified entry: test/lint/build/test-postgres/test-mysql/test-db-matrix), whose `make help` verification is now partially scaffolded by the existing minimal Makefile.

### Session 005 · 2026-08-09

#### Goal

- Implement `M0-CMD-001`: root Makefile unified entry (`make help` listing test/lint/build/test-postgres/test-mysql/test-db-matrix), TDD-driven with a reproducible gate test.

#### Checkpoint 16 · M0-CMD-001 implemented and passed its gate

- Feature: `M0-CMD-001`
- Status change: `not_started` → `in_progress` → `passing` (same session).
- Result:
  1. TDD — wrote `scripts/validate-make-help.test.js` (7 dependency-free node:test cases) first and confirmed all 7 RED (`make help` had no rule, targets missing), then implemented the Makefile until 7/7 GREEN.
  2. `Makefile` (extended from the `M0-ENV-002` minimal version): `help` target documenting the six unified commands + `validate-env-example`; `test` = backend pytest (venv) with a printed note that the frontend test leg lands with `M0-FE-001`; `lint` = backend `ruff check src tests` + frontend `npm run lint`; `build` = backend `compileall -q src` + frontend `tsc && vite build`; `test-postgres`/`test-mysql`/`test-db-matrix` exit non-zero with an explicit prerequisite notice rather than false-passing until `M0-BOOT-001` compose + `M1-DB-*` dialect contracts exist.
  3. Environment: GNU Make 4.4.1 (chocolatey); backend venv tools invoked via `.venv/Scripts/` (uv unavailable in WSL bash); frontend `npm run lint` and `npm run build` both pass with no working-tree side effects.
- Files changed: `Makefile`, `scripts/validate-make-help.test.js` (new), `feature_list.json`, `progress.md`.
- Verification: `node --test scripts/validate-make-help.test.js` → 7/7 pass (make help lists all six; test/lint/build exit 0; db-matrix trio refuses with notice); `make help` exits 0; regression `make validate-env-example` exits 0 and `node scripts/validate-feature-list.js` exits 0 (7 passing, 0 in_progress after transition).
- Evidence: recorded on `M0-CMD-001` in `feature_list.json`.
- Decision: The db-matrix targets are honest guards, not stubs that lie — they fail fast with a prerequisite message and will be rewritten by the features that build compose/dialects. The `make test` frontend leg is deliberately deferred to `M0-FE-001` (its declared verification is `cd frontend && npm run test -- --run`), recorded as a cross-feature handoff.
- Next action: Start `M0-BOOT-001` (本地 Compose 编排：database/redis/backend/worker/frontend), whose `docker compose config` verification depends on the now-typed config from `M0-ENV-001` and `.env.example` from `M0-ENV-002`.

### Session 006 · 2026-08-09

#### Goal

- Implement `M0-BOOT-001`: local Compose orchestration of database, redis, backend, worker and frontend, verified by `docker compose config` (services all present and the configuration parses).

#### Checkpoint 17 · M0-BOOT-001 activated

- Feature: `M0-BOOT-001`
- Status change: `not_started` → `in_progress`.
- Result: Baseline confirmed — no compose file or Dockerfile exists anywhere in the repo; backend is the FastAPI scaffold (`src/litemcp/main.py`, `/livez` + `/readyz` only) with no uvicorn entry script and no `src/litemcp/workers/` module; frontend is the Vite dev starter; `LITEMCP_DATABASE_URL`/`LITEMCP_REDIS_URL`/`LITEMCP_ENCRYPTION_KEYS` are required config. Docs (`00-overview.md` §5.3/§7/§9, `08-implementation-plan.md`) mandate: five services (database/Redis/backend/worker/frontend), backend and worker share one application image with different commands, default database profile is PostgreSQL (MySQL stays out of scope here), and the root Makefile is the unified management entry. Docker 28.4.0 + Compose v2.39.4 available, so the declared `docker compose config` verification is runnable.
- Files changed: `feature_list.json` (status), `progress.md`.
- Verification: `node scripts/validate-feature-list.js` exits 0 (1 in_progress, 7 passing, 0 blocked).
- Next action: Dispatch the isolated test-writer to write `scripts/validate-compose.test.js` (gate test driving the compose file's existence and five-service structure).

#### Checkpoint 18 · M0-BOOT-001 implemented and passed its gate

- Feature: `M0-BOOT-001`
- Status change: `in_progress` → `passing` (same session).
- Result:
  1. Isolated TDD per AGENTS.md: test-writer subagent produced `scripts/validate-compose.test.js` (4 dependency-free node:test cases); controller confirmed RED in-session (`docker compose config` → `no configuration file provided: not found`), then a fresh implementer subagent built the implementation without seeing the test-writer's reasoning. Test commit `eb938d8`.
  2. `docker-compose.yml` (repo root) with exactly five services: `database` (postgres:16-alpine, healthcheck+volume), `redis` (redis:7-alpine), `backend` (`build: ./backend` + `image: litemcp-app:dev`, uvicorn `litemcp.main:app` on :8000), `worker` (same image, `command: python -m litemcp.workers`, placeholder), `frontend` (node:22-alpine, Vite dev on :5173). Every `${VAR}` has an inline `:-` default so `docker compose config` parses with no `.env` file; compose service names wired into `LITEMCP_DATABASE_URL`/`LITEMCP_REDIS_URL`; `LITEMCP_ENVIRONMENT=dev`; dev Fernet placeholder key with generation comment.
  3. `backend/Dockerfile` + `.dockerignore` (excludes the 44k-file `.venv`/tests/caches): installs pinned `uv 0.11.18`, `uv sync --frozen --no-dev` builds+installs the `uv_build`-backed package, CMD uvicorn. `frontend/Dockerfile` + `.dockerignore` (excludes `node_modules`/`dist`): `npm ci` from lockfile, Vite dev.
  4. `backend/src/litemcp/workers/__init__.py` + `__main__.py`: minimal placeholder worker entrypoint (`python -m litemcp.workers`) that logs "M0-BOOT-001 placeholder" and stays alive; real worker jobs (build/sync/GC/key rotation) are M3 scope.
- Files changed: `docker-compose.yml`, `backend/Dockerfile`, `backend/.dockerignore`, `backend/src/litemcp/workers/__init__.py`, `backend/src/litemcp/workers/__main__.py`, `frontend/Dockerfile`, `frontend/.dockerignore`, `feature_list.json`, `progress.md`.
- Verification: `node --test scripts/validate-compose.test.js` → 4/4 GREEN (RED confirmed earlier). `docker compose config` exits 0; `--services` lists backend/database/frontend/redis/worker. `docker compose up -d --wait` → all five services healthy; backend `/livez` HTTP 200 `{"status":"ok"}`, frontend `/` HTTP 200, worker placeholder log line present; `docker compose down -v` cleaned up. Backend image builds (`uv sync --frozen`, 68 locked deps, `litemcp==0.1.0`) and frontend image builds (`npm ci`). `node scripts/validate-feature-list.js` exits 0 (7 passing, 1 in_progress before transition). ruff/mypy clean on the new worker package (reported by implementer subagent; config-only change).
- Evidence: recorded on `M0-BOOT-001` in `feature_list.json`.
- Decision: Worker is a minimal runnable placeholder (M3 reuses/replaces it). MySQL compose profile is intentionally out of scope (single relational DB per environment per 00-overview §5.3). `make dev`/`make docker-down` (00-overview §9) are documented but NOT added to the Makefile here — M0-CMD-001 owns the Makefile gate; flagging as a candidate follow-up feature rather than silently expanding M0-BOOT-001's surface. `backend/uv.lock` (already tracked from the scaffold) is what makes `uv sync --frozen` reproducible in the image.
- Next action: Next highest-priority feature is `M0-BE-001` (后端存活检查契约 `tests/api/test_health.py -k livez`) — the first feature that exercises real backend API behavior via pytest, per the startup selection rule.

### Session 007 · 2026-08-09

#### Goal

- User asked whether ADR (Architecture Decision Record) practice can be configured now given the project follows agile process. Confirmed yes and that `08-implementation-plan.md` already mandates ADRs covering six specific M0 topics as an M0 deliverable with no infrastructure yet in the repo. Added `M0-ADR-001` (priority 20, out-of-band addition, not part of the original 141-feature inventory) to close that gap: ADR directory, template, and 6 ADRs sourced from existing architecture-doc decisions, plus a structural validator script.

#### Checkpoint 19 · M0-ADR-001 added and activated

- Feature: `M0-ADR-001`
- Status change: new → `in_progress` (created and activated in the same session, since no feature was active).
- Result: Added the feature to `feature_list.json` at priority 20 (after `M0-CI-001`, before the M1 block), area `harness`, depending on `M0-HARNESS-002` (the checkpoint protocol ADRs will be cross-referenced from). Verification declared as `node scripts/validate-adr.js` (automated structural check: required sections present, all six M0 topics covered) plus a manual traceability review confirming each ADR's Decision matches an already-stated position in `docs/architecture/*.md` rather than inventing a new one.
- Files changed: `feature_list.json`, `progress.md`.
- Verification: `node scripts/validate-feature-list.js` exits 0 (39 features, 8 passing, 1 in_progress, 0 blocked).
- Next action: Research the six required ADR topics across `docs/architecture/00-10*.md` to extract already-stated decisions (not invent new ones), then create `docs/adr/` with template + README + 6 ADR files, then TDD the `scripts/validate-adr.js` gate.

#### Checkpoint 20 · M0-ADR-001 implemented and passed its gate

- Feature: `M0-ADR-001`
- Status change: `in_progress` → `passing` (same session).
- Result:
  1. Dispatched a foreground Explore subagent (read-only) to trace all 6 M0-mandated ADR topics to exact `docs/architecture/*.md` file:line citations, including which parts are stated as `[既定]`/committed vs `[建议]`/`[后续]`/provisional, and — for the reverse-proxy topic — confirming the concrete topology is explicitly listed as undecided until M2 entry (`08-implementation-plan.md` L140), not something to invent here.
  2. `docs/adr/template.md` — Nygard-style template (Status/Context/Decision/Consequences) plus a `Source refs` line requirement so every ADR must cite where its decision comes from. `docs/adr/README.md` — practice guide (when to write an ADR, naming `NNNN-slug.md`, why-decisions-not-implementation-details) and an index table of all 6.
  3. 6 ADR files, each citing precise `docs/architecture/*.md` line ranges and separating committed content from provisional `[建议]`/`[后续]` content with its trigger condition: `0001-mcp-sdk-version-pinning.md` (SDK pinned to protocol 2025-11-25, version-adapter boundary for the 2026-07-28 breaking change deferred), `0002-db-dialect-strategy.md` (PostgreSQL 14+ / MySQL 8.0+ both first-tier/gating, SQLite rejected as a dialect-compatibility substitute, MariaDB/SQL Server explicitly second-tier/undated), `0003-outbox-worker-reliability.md` (at-least-once + reentrant handler + `generation` CAS; persistent `idempotency_record` explicitly deferred pending observed retry behavior), `0004-feature-flag-registry.md` (small in-house typed registry with safe-false defaults; OpenFeature provider explicitly deferred until a remote control plane is needed), `0005-object-storage-registry-interface.md` (`StorageBackend` abstraction + immutable per-revision OCI image run by digest, never mutable tags; microVM/gVisor sandboxing explicitly deferred), `0006-reverse-proxy-trusted-proxy.md` (only-explicitly-configured-proxy trust principle is committed, but the file's own Status line and Consequences section flag that concrete topology/TLS-termination is undecided until M2 entry — deliberately not invented here).
  4. TDD: `scripts/validate-adr.test.js` (8 `node:test` cases) written first; confirmed RED (`Cannot find module './validate-adr.js'`); then implemented `scripts/validate-adr.js` (pure `validateAdrSet(files)` function + CLI wrapper, same shape as `validate-env-example.js`) until 8/8 GREEN. Checks: numbered-ADR filename pattern, required `- Status:` line, the 3 required section headers, minimum count of 6, and each of the 6 required topics matched by at least one ADR's content via `REQUIRED_TOPICS` patterns.
  5. `Makefile` — added `validate-adr` target (mirrors `validate-env-example`) and listed it under `make help`'s "Other targets".
- Files changed: `docs/adr/README.md` (new), `docs/adr/template.md` (new), `docs/adr/0001..0006-*.md` (new, 6 files), `scripts/validate-adr.js` (new), `scripts/validate-adr.test.js` (new), `Makefile`, `feature_list.json`, `progress.md`.
- Verification: `node --test scripts/validate-adr.test.js` → 8/8 GREEN (RED confirmed first). `node scripts/validate-adr.js` / `make validate-adr` → exits 0 against the real `docs/adr/` content ("6 numbered ADR(s) covering all 6 required M0 topics"). Manual negative injection: temporarily removed `0006-reverse-proxy-trusted-proxy.md` → gate failed with exactly the expected 2 errors (count below 6, missing "production reverse proxy & trusted proxy" topic); restored the file → gate passed again. Regression: `node scripts/validate-feature-list.js` exits 0 (39 features, 9 passing, 0 in_progress); `make validate-env-example` still exits 0; `make help` lists the new target.
- Evidence: recorded on `M0-ADR-001` in `feature_list.json`.
- Decision: The split test-writer/implementer subagent workflow from `AGENTS.md` was intentionally skipped for this feature — the validator's behavior (file-existence/section/topic-keyword checks) is config-like and low-risk of "implementation fitted to its own test," matching the documented exemption for trivial/config-only changes; TDD was still done in-session (RED confirmed before implementation). ADR-0006 is deliberately the only one of the six marked with an unresolved item in its Status/Consequences, rather than silently inventing a reverse-proxy topology the architecture docs don't yet commit to.
- Next action: Next highest-priority feature is `M0-BE-001` (后端存活检查契约 `tests/api/test_health.py -k livez`), unchanged from before this out-of-band ADR feature was inserted.

### Session 008 · 2026-08-09

#### Goal

- Implement `M0-BE-001` (后端存活检查契约) via the AGENTS.md isolated TDD workflow: test-writer and implementer dispatched as separate subagents with isolated context, RED/GREEN verified by the controlling session.

#### Checkpoint 21 · M0-BE-001 activated

- Feature: `M0-BE-001`
- Status change: `not_started` → `in_progress`.
- Result: Baseline confirmed before dispatch. `backend/src/litemcp/main.py` already exposes `GET /livez` → `{"status": "ok"}` (200), so the target behavior exists in the scaffold; `backend/tests/` contains only `core/` — `tests/api/` does not exist yet, so the feature's declared verification (`cd backend && uv run pytest tests/api/test_health.py -k livez`) has no test to run. Architecture-doc contract for `/livez` reviewed: `07-observability.md` L418 (`/livez` must not touch external dependencies; fails only when the process is unrecoverable) and `08-implementation-plan.md` L100 (live reflects process liveness; ready reflects dependencies). No documented response structure richer than `{"status": "ok"}`.
- Files changed: `feature_list.json` (status), `progress.md`.
- Verification: `node scripts/validate-feature-list.js` exits 0 (1 in_progress, 9 passing, 0 blocked).
- Known risk: Because the scaffold already implements the behavior, the isolated RED step may be unobservable as "missing behavior" — the test may be GREEN on first run. This is recorded honestly rather than forcing an artificial RED.
- Next action: Dispatch the isolated test-writer subagent to write `backend/tests/api/test_health.py` (livez contract tests) from the feature's behavior/verification/source_refs alone.

#### Checkpoint 22 · M0-BE-001 implemented and passed its gate

- Feature: `M0-BE-001`
- Status change: `in_progress` → `passing` (same session).
- Result (isolated TDD split per AGENTS.md):
  1. Test-writer subagent (isolated context: only `behavior`/`verification`/`source_refs`, forbidden from reading `backend/src/`) produced `backend/tests/api/test_health.py` — 3 contract tests: HTTP 200 when alive, `application/json` content-type, stable payload `{"status": "ok"}`. It reported only the file path.
  2. Controlling-session RED run: the test was **GREEN on first run** (3 passed). RED was unobservable as "missing behavior" because the scaffold's `backend/src/litemcp/main.py` already exposes `GET /livez` → `{"status": "ok"}`. Recorded honestly — the isolation still holds (test derived from behavior + architecture docs alone), but there was no missing behavior to fail on.
  3. Fresh implementer subagent (test file path + behavior text only) confirmed the existing `main.py` already satisfies all 3 assertions → **zero implementation change**; no implementer commit exists by design.
- Files changed: `backend/tests/api/test_health.py` (new), `feature_list.json` (status → passing + evidence), `progress.md`.
- Verification: Declared `cd backend && .venv/Scripts/python.exe -m pytest tests/api/test_health.py -k livez` → 3 passed; full backend suite 20 passed (17 config + 3 livez); `ruff check src tests` clean; `node scripts/validate-feature-list.js` exits 0 (10 passing, 0 in_progress after transition).
- Evidence: recorded on `M0-BE-001` in `feature_list.json` (test / validation / regression entries).
- Commits: `8b0c693` `test(api): add livez contract gate test (M0-BE-001)`; state commit follows.
- Decision: The isolated split was still exercised end-to-end per user request even though this feature is contract-pinning (implementation pre-exists). The RED unobservability is a feature of "verify existing contract" work, not a process failure — recorded as such. `tests/api/test_health.py` will be extended with `/readyz` cases by `M0-BE-002` (same file, `-k readyz`).
- Next action: `M0-BE-002` (后端就绪检查 `tests/api/test_health.py -k readyz`), depends on `M0-ENV-001` (passing).

### Session 009 · 2026-08-09

#### Goal

- Implement `M0-BE-002` (后端就绪检查) via the AGENTS.md isolated TDD workflow: test-writer and implementer dispatched as separate subagents with isolated context, RED/GREEN verified by the controlling session.

#### Checkpoint 23 · M0-BE-002 activated

- Feature: `M0-BE-002`
- Status change: `not_started` → `in_progress`.
- Result: Baseline confirmed before dispatch. `backend/src/litemcp/main.py` exposes `GET /readyz` but only as a static scaffold placeholder returning `{"status": "ok"}` — no real dependency probing. `backend/tests/api/test_health.py` contains only the 3 `-k livez` contract tests; no `-k readyz` cases exist yet. Backend deps already include `sqlalchemy[asyncio]`, `asyncpg`, `redis>=5.1`, so real DB/Redis probes are implementable without new dependencies. Readiness contract per `07-observability.md`: L26 (`/readyz` judges whether the instance can safely accept traffic), L419 (backend `/readyz` checks DB, necessary Redis Session capability, local config/keys; Redis rate-limit-only failure keeps ready but degraded), L420 (health responses only expose component/status/reason code — no DSN, host, stack, version, or service list). `asyncio_mode = "strict"` is configured, so async tests need pytest-asyncio markers.
- Files changed: `feature_list.json` (status), `progress.md`.
- Verification: `node scripts/validate-feature-list.js` exits 0 (1 in_progress, 10 passing, 0 blocked).
- Next action: Dispatch the isolated test-writer subagent to write the `-k readyz` cases in `backend/tests/api/test_health.py` from the feature's behavior/verification/source_refs alone.

#### Checkpoint 24 · M0-BE-002 implemented and passed its gate

- Feature: `M0-BE-002`
- Status change: `in_progress` → `passing` (same session).
- Result (isolated TDD split per AGENTS.md):
  1. Test-writer subagent (isolated context: only `behavior`/`verification`/source_refs, forbidden from reading `backend/src/`) appended `TestReadyzContract` (3 tests) to `backend/tests/api/test_health.py` and defined the failure-injection seam: two sync module-level bool probes `probe_database()`/`probe_redis()` on `litemcp.main`, resolved at request time so monkeypatch is observable. Response contract: 200/`{"status":"ready","components":[...]}` when both healthy; 5xx/`{"status":"not_ready"}` when either down; each component exactly `{name,status,reason}` with status mirroring the probe; body leaks no DSN/host/stack/secrets (07-observability.md L420, pinned via `_assert_no_sensitive_or_operational_leak`).
  2. Controlling-session RED run: `-k readyz` → 3 failed, all on `AttributeError: module 'litemcp.main' has no attribute 'probe_database'` — the missing real-readiness seam, not a typo or broken setup.
  3. Fresh implementer subagent (test file + behavior text only) changed only `backend/src/litemcp/main.py`: added `probe_database()` (async `SELECT 1` via SQLAlchemy async engine wrapped in a sync bool contract, 2.0s timeout, fail-closed False) and `probe_redis()` (sync `redis.Redis.ping()`, 2.0s socket timeouts, fail-closed False); `/readyz` handler resolves probes at request time and runs them via `asyncio.to_thread` so the event loop never blocks and the sync `asyncio.run`-based DB probe runs on a loop-free worker thread; returns 200 `{"status":"ready"}` or 503 `{"status":"not_ready"}`; `/livez` untouched.
- Files changed: `backend/tests/api/test_health.py` (test-writer), `backend/src/litemcp/main.py` (implementer), `feature_list.json` (status → passing + evidence + implementation files), `progress.md`.
- Verification: `pytest tests/api/test_health.py -k readyz -v` → 3 passed (GREEN; RED confirmed earlier). Full suite `pytest -q` → 23 passed (17 config + 3 livez + 3 readyz), no regression. `ruff check src tests` clean (4 `# noqa: BLE001` probe fail-closed catches). `mypy src` clean (1 `# type: ignore[arg-type]` on `asyncio.run`). Real-probe smoke (unmonkeypatched, unreachable loopback:1) → `probe_database()` False in ~2.05s, `probe_redis()` False in ~2.01s, no exception/hang. `/livez` unchanged (200 + `{"status":"ok"}`, no dependency probing). `node scripts/validate-feature-list.js` exits 0 (10 passing, 1 in_progress before transition).
- Evidence: recorded on `M0-BE-002` in `feature_list.json`.
- Commits: `a67c10f` `test(api): add readyz contract gate test (M0-BE-002)`; `4d3a50b` `feat(api): implement real /readyz dependency probes (M0-BE-002)`; `5e943bd` `feat(api): mark M0-BE-002 readyz contract passing with evidence (M0-BE-002)`.
- Decision: Real DB/Redis probes are deliberately fail-closed with short timeouts, and reason codes carry only `ok`/`connection_failed` (no DSN/host) per 07-observability L420. The probe seam (`probe_database`/`probe_redis`) is the durable contract that M1+ code will reuse once the real SQLAlchemy session factory (M1-DB-001) exists. Redis "rate-limit-only → degraded" semantics (L419) are deferred to when Redis rate-limiting exists (M4-LIMIT-*), out of this feature's scope.
- Next action: `M0-BE-003` (关联 ID 中间件 `tests/middleware/test_correlation.py`, priority 16), unchanged from the plan.

### Session 010 · 2026-08-09

#### Goal

- Implement `M0-BE-003` (关联 ID 中间件) via the AGENTS.md isolated TDD workflow: test-writer and implementer dispatched as separate subagents with isolated context, RED/GREEN verified by the controlling session.

#### Checkpoint 25 · M0-BE-003 activated

- Feature: `M0-BE-003`
- Status change: `not_started` → `in_progress`.
- Result: Baseline confirmed. `backend/tests/` has no `middleware/` directory (the feature's declared verification `pytest tests/middleware/test_correlation.py` has no test to run yet); `backend/src/litemcp/main.py` has no correlation middleware. Contract from `07-observability.md`: L75 (`request_id` — ingress validates `X-Request-ID`: only `[A-Za-z0-9._-]`, 1–128 bytes; invalid values are regenerated; response echoes the ID), L80 (HTTP ingress always generates an independent `request_id`), L227 (log correlation relies on ID fields rather than string interpolation); and `05-agent-gateway.md` L408 (responses uniformly include `X-Request-Id`). `correlation_id` (L76) is a server-generated stable ID for one business workflow (build/sync/GC), distinct from the per-request `request_id`.
- Files changed: `feature_list.json` (status), `progress.md`.
- Verification: `node scripts/validate-feature-list.js` exits 0 (1 in_progress, 11 passing, 0 blocked).
- Next action: Dispatch the isolated test-writer subagent to write `tests/middleware/test_correlation.py` from the feature's behavior/verification/source_refs alone.

#### Checkpoint 26 · M0-BE-003 implemented and passed its gate

- Feature: `M0-BE-003`
- Status change: `in_progress` → `passing` (same session).
- Result (isolated TDD split per AGENTS.md):
  1. Test-writer subagent (isolated context, forbidden from reading `backend/src/`) created `backend/tests/middleware/test_correlation.py` — 16 tests across three classes pinning the docs/07 L75/L80 + docs/05 L408 contract: (a) ingress validation (valid `X-Request-ID` accepted/echoed, 128-byte boundary, 6 invalid variants + absent regenerated to a valid ID), (b) uniform response echo on probe/`/livez`/404, (c) context propagation via `request.state.request_id` observed through a test-only probe route registered on the imported app. The probe route is test scaffolding entirely inside the test file.
  2. Controlling-session RED run: `pytest tests/middleware/test_correlation.py` → 16 failed, all on `AttributeError: 'State' object has no attribute 'request_id'` / absent `X-Request-Id` — the missing-middleware behavior, not a typo or setup issue.
  3. Fresh implementer subagent (test file + behavior text only) added `backend/src/litemcp/correlation.py` (`CorrelationIdMiddleware`, Starlette `BaseHTTPMiddleware`): validates inbound `X-Request-ID` (`[A-Za-z0-9._-]+`, 1–128 bytes), regenerates on absent/invalid via `req_` + `secrets.token_hex(16)`, sets `request.state.request_id`, echoes `X-Request-Id` on every response; mounted in `main.py` via `app.add_middleware(...)`. `/livez` and `/readyz` untouched.
- Files changed: `backend/tests/middleware/test_correlation.py` (test-writer), `backend/src/litemcp/correlation.py` (new, implementer), `backend/src/litemcp/main.py` (implementer), `feature_list.json` (status → passing + evidence + implementation files), `progress.md`.
- Verification: `pytest tests/middleware/test_correlation.py` → 16 passed (GREEN; RED confirmed earlier). Full suite `pytest -q` → 39 passed (17 config + 3 livez + 3 readyz + 16 correlation), no regression. `ruff check src tests` clean. `mypy src` clean (6 source files). `node scripts/validate-feature-list.js` exits 0 (11 passing, 1 in_progress before transition).
- Evidence: recorded on `M0-BE-003` in `feature_list.json`.
- Commits: `7a81a30` `test(api): add correlation-id middleware contract tests (M0-BE-003)`; `4f8511b` `feat(api): add correlation-id middleware (M0-BE-003)`; `079aaa5` `feat(api): mark M0-BE-003 correlation middleware passing with evidence (M0-BE-003)`.
- Decision: The middleware is a pure per-request concern (no config/deps); `request.state.request_id` is the propagation channel per the test contract, and a contextvar/accessor layer is not needed at M0. The `correlation_id` concept (07 L76, server-generated stable workflow ID) is intentionally not implemented here — it attaches to async build/sync/GC workflows (M3), not per-request HTTP handling; recorded so no later feature silently reuses `request_id` as a workflow ID.
- Next action: `M0-FE-001` (前端测试脚手架 `cd frontend && npm run test -- --run`, priority 17) — the first frontend feature; its test leg was explicitly deferred from `make test` (M0-CMD-001) until now.

### Session 011 · 2026-08-09

#### Goal

- Implement `M0-FE-001` (前端测试脚手架) via the AGENTS.md isolated TDD workflow: test-writer and implementer dispatched as separate subagents with isolated context, RED/GREEN verified by the controlling session.

#### Checkpoint 27 · M0-FE-001 activated

- Feature: `M0-FE-001`
- Status change: `not_started` → `in_progress`.
- Result: Baseline confirmed. Frontend has NO test infrastructure: `package.json` has no `test` script (only dev/build/lint/preview), no vitest config in `vite.config.ts`, no test files anywhere, and no RTL/MSW deps. Scaffold components are static (no fetch/axios/useQuery in `frontend/src/`), so MSW "basic config" must be proven via interception inside a test rather than a real page fetch. Architecture mandate from `06-frontend.md` L55: 测试使用 Vitest、React Testing Library、MSW、Playwright 和 axe-core（或等价可自动化无障碍检查器）；M0-FE-001 scope is the first three + a minimal component test (Playwright/axe land with later frontend features). Version compatibility confirmed: installed vite 8.0.16; `vitest@4.1.10` peer-accepts `^6.0.0 || ^7.0.0 || ^8.0.0`, so the implementer will pin vitest@^4 + jsdom + RTL + MSW@^2.
- Files changed: `feature_list.json` (status).
- Verification: `node scripts/validate-feature-list.js` exits 0 (1 in_progress, 12 passing, 0 blocked).
- Next action: Dispatch the isolated test-writer subagent to write the minimal component test(s) from the feature's behavior/verification/source_refs alone.

#### Checkpoint 28 · M0-FE-001 implemented and passed its gate

- Feature: `M0-FE-001`
- Status change: `in_progress` → `passing` (same session).
- Result (isolated TDD split per AGENTS.md):
  1. Test-writer subagent (isolated context: only `behavior`/`verification`/source_refs; allowed to read existing frontend components to pick a real subject) produced `frontend/src/test/navbar.test.tsx` (real `Navbar` RTL test: brand, 5 top-level nav links with href, Sponsor button, Search placeholder, mobile toggle open/close via `aria-expanded`, with a jsdom `window.matchMedia` stand-in for HeroUI's useTheme) and `frontend/src/test/msw.test.tsx` (real MSW 2.x interception proof via a test-only fetch component: mocked data renders, per-test `server.use()` override, unhandled requests blocked under `onUnhandledRequest:'error'`). It reported only the two file paths.
  2. Controlling-session RED run: `npm run test -- --run` → `npm error Missing script: "test"` — the absent test scaffold, not a typo or setup issue.
  3. Fresh implementer subagent (test file paths + behavior text only, no test-writer reasoning) set up the stack without touching test assertions: `npm install -D vitest@^4 jsdom @testing-library/react @testing-library/dom @testing-library/jest-dom@^6 msw@^2` (jest-dom re-pinned to ^6.10.0 after bare install pulled v7), added `"test": "vitest"` to `frontend/package.json`, and extended `vite.config.ts` (defineConfig from `vitest/config`, `test: { environment: "jsdom" }`) keeping `resolve.tsconfigPaths` so the `@/` alias is inherited. Reported 7/7 GREEN (GREEN re-verified by the controlling session: 2 files / 7 tests, exit 0).
- Files changed: `frontend/src/test/navbar.test.tsx`, `frontend/src/test/msw.test.tsx` (test-writer), `frontend/package.json`, `frontend/package-lock.json`, `frontend/vite.config.ts` (implementer), `feature_list.json` (status → passing + evidence), `progress.md`.
- Verification: declared `cd frontend && npm run test -- --run` → 2 files / 7 tests passed. Regression: `npm run build` (tsc + vite) exit 0 (test files compile under strict TS), `npm run lint` exit 0 (only prettier reformats on test files, assertions unchanged), `node scripts/validate-feature-list.js` exits 0 (12 passing, 1 in_progress before transition). Version-compat: vite 8.0.16 (unchanged) + vitest 4.1.10 (peer-accepts vite ^8) + jsdom 29.1.1 + RTL 16.3.2 + jest-dom 6.10.0 + msw 2.15.0.
- Evidence: recorded on `M0-FE-001` in `feature_list.json`.
- Commits: `f34760a` `test(frontend): add vitest/RTL/MSW scaffold tests (M0-FE-001)`; `4a9f912` `feat(frontend): add vitest config and dev deps for test scaffold (M0-FE-001)`; state commit follows.
- Decision: This completes the frontend test leg that `make test` (M0-CMD-001) deliberately deferred; `make test` now covers backend pytest + the new `npm run test -- --run` frontend leg when it is wired in (make test currently still prints the deferral note — updating the root Makefile test target is a candidate narrow change but out of this feature's declared verification). Playwright + axe-core (06-frontend.md L55) land with later frontend features, not M0.
- Next action: `M0-CONTRACT-001` (OpenAPI 快照门禁 `make test-openapi`, priority 18, depends on passing `M0-BE-001`) — the next M0 feature after M0-FE-001 completes.

### Session 012 · 2026-08-09

#### Goal

- Implement `M0-CONTRACT-001` (OpenAPI 快照门禁) via the AGENTS.md isolated TDD workflow: test-writer and implementer dispatched as separate subagents with isolated context, RED/GREEN verified by the controlling session.

#### Checkpoint 29 · M0-CONTRACT-001 activated

- Feature: `M0-CONTRACT-001`
- Status change: `not_started` → `in_progress`.
- Result: Baseline confirmed. Backend FastAPI app generates OpenAPI 3.1.0 (via `app.openapi()`), currently exactly two paths: `/livez` (operationId `livez_livez_get`) and `/readyz` (`readyz_readyz_get`); importing `litemcp.main` and calling `openapi()` needs no env config. NO snapshot file exists anywhere; root Makefile has no `test-openapi` target (only validate-env-example/validate-adr in "Other targets"). Contract requirements from docs: `09-verification.md` L25/L139 (OpenAPI diff is a per-commit blocker; generate from FastAPI, breaking diff must be explicitly approved and frontend generated code/fixture synced) and L131 (OpenAPI 生成差异 is a 阻断项), `08-implementation-plan.md` L334 (OpenAPI snapshot/breaking diff in PR quick gate), `06-frontend.md` L449 (frontend will generate types from backend OpenAPI — needs a stable committed snapshot path). Design: commit `backend/src/litemcp/openapi.json` snapshot (deterministic sort_keys serialization), gate test at `backend/tests/contract/test_openapi_snapshot.py` regenerates `app.openapi()` and deep-compares to the committed snapshot (drift → fail, incl. a negative case proving the gate catches an unapproved change), and a regeneration path (`make update-openapi-snapshot`) so intentional changes can be explicitly approved. `make test-openapi` runs the gate test.
- Files changed: `feature_list.json` (status).
- Verification: `node scripts/validate-feature-list.js` exits 0 (1 in_progress, 13 passing, 0 blocked).
- Next action: Dispatch the isolated test-writer subagent to write `backend/tests/contract/test_openapi_snapshot.py` from the feature's behavior/verification/source_refs alone.

#### Checkpoint 30 · M0-CONTRACT-001 implemented and passed its gate

- Feature: `M0-CONTRACT-001`
- Status change: `in_progress` → `passing` (same session).
- Result (isolated TDD split per AGENTS.md):
  1. Test-writer subagent (isolated context: only `behavior`/`verification`/source_refs, forbidden from reading `backend/src/`) produced `backend/tests/contract/test_openapi_snapshot.py` — 4 tests: (a) committed snapshot exists at `backend/src/litemcp/openapi.json`; (b) valid OpenAPI 3.1.0 with `info` + paths `/livez`/`/readyz`; (c) the drift gate — spec regenerated NOW from `app.openapi()` deep-equals the committed snapshot (parsed-JSON equality, never raw bytes); (d) negative case — the same comparison raises `AssertionError` matching "OpenAPI drift detected" when the live spec gains an unapproved path (monkeypatched `app.openapi`). It reported only the file path.
  2. Controlling-session RED run: `pytest tests/contract/test_openapi_snapshot.py` → 4 failed, all FileNotFoundError on the missing snapshot — the absent gate, not a typo.
  3. Fresh implementer subagent (test file path + behavior text only) generated `backend/src/litemcp/openapi.json` from the running app (deterministic `json.dumps(indent=2, sort_keys=True)` + trailing newline, never hand-written), added `backend/scripts/regenerate_openapi.py` (byte-identical regeneration), and added `test-openapi` + `update-openapi-snapshot` targets to the root Makefile "Other targets" (six unified commands and existing validate targets untouched). Reported 4/4 GREEN; re-verified by the controlling session.
- Files changed: `backend/tests/contract/test_openapi_snapshot.py`, `backend/tests/middleware/test_correlation.py` (test-writer + isolation fix), `backend/src/litemcp/openapi.json`, `backend/scripts/regenerate_openapi.py`, `Makefile` (implementer), `feature_list.json` (status → passing + evidence), `progress.md`.
- Verification: `make test-openapi` → 4 passed. Full backend suite `pytest -q` → 43 passed (39 + 4), no regression. `ruff check src tests` clean. `node --test scripts/validate-make-help.test.js` → 7/7 (M0-CMD-001 gate unaffected). Regeneration round-trip byte-stable (sha256 `def7b9fbc...` identical before/after `make update-openapi-snapshot`; `git diff --exit-code` clean). `node scripts/validate-feature-list.js` exits 0 (13 passing, 1 in_progress before transition).
- Evidence: recorded on `M0-CONTRACT-001` in `feature_list.json`.
- Commits: `41614fc` `test(contract): add OpenAPI snapshot gate tests, isolate probe route (M0-CONTRACT-001)`; `6220b63` `feat(contract): add OpenAPI snapshot, regen script, make gates (M0-CONTRACT-001)`; state commit follows.
- Decision (cross-module isolation fix, adjudicated by the controller): the M0-BE-003 correlation test registered its test-only probe route at module import time, which leaked into the shared `app.openapi()` and broke this feature's drift gate (full suite ran red on `Diverging sections: ['paths']`). The implementer moved the probe route into a module-scoped autouse fixture that registers lazily and removes it (plus `app.openapi_schema = None`) in teardown — a legitimate test-isolation improvement, no assertions changed, all 16 correlation tests still pass. A cosmetic ruff I001 on the new contract test (double blank line after imports) was resolved by deleting one blank line instead of a permanent per-file-ignore; `pyproject.toml` left unchanged.
- Next action: `M0-CI-001` (基础 CI 门禁 `make ci-fast`, priority 19, depends on passing `M0-CMD-001` + `M0-FE-001`) — completes the M0 engineering baseline; then M1 data layer begins with `M1-DB-001`.

### Session 013 · 2026-08-09

#### Goal

- Implement `M0-CI-001` (基础 CI 门禁) via the AGENTS.md isolated TDD workflow: test-writer and implementer dispatched as separate subagents with isolated context, RED/GREEN verified by the controlling session.

#### Checkpoint 31 · M0-CI-001 activated

- Feature: `M0-CI-001`
- Status change: `not_started` → `in_progress`.
- Result: Baseline confirmed. No `.github/` directory (no CI workflow exists anywhere). Backend venv already has ruff + mypy 2.3.0 + pytest as dev deps; `frontend/tsconfig.json` has `noEmit: true` so bare `tsc` is a type check. `make ci-fast` target does NOT exist. Scope per `08-implementation-plan.md` L93/L327-335 (PR 快速门禁: Python lint/format/type/unit, TypeScript lint/type/unit/build, plus OpenAPI snapshot etc.) — this feature's declared behavior is exactly the seven legs 后端 lint/type/unit (ruff/mypy/pytest) + 前端 lint/type/unit/build (eslint/tsc/vitest/vite build), delivered as the local-equivalent `make ci-fast` gate. Document-link check / secret scan / dependency review (L93 broader CI) and a GitHub Actions workflow are recorded as candidates, not this feature's scope (verification is the local `make ci-fast`).
- Files changed: `feature_list.json` (status).
- Verification: `node scripts/validate-feature-list.js` exits 0 (1 in_progress, 14 passing, 0 blocked).
- Next action: Dispatch the isolated test-writer subagent to write `scripts/validate-ci-fast.test.js` (node:test gate driving the Makefile ci-fast target's existence, discoverability, exit-0, and leg-marker honesty check).

#### Checkpoint 32 · M0-CI-001 implemented and passed its gate

- Feature: `M0-CI-001`
- Status change: `in_progress` → `passing` (same session).
- Result (isolated TDD split per AGENTS.md):
  1. Test-writer subagent (isolated context: only `behavior`/`verification`/source_refs) produced `scripts/validate-ci-fast.test.js` — 3 node:test cases: (a) `make help` lists `ci-fast`; (b) `make ci-fast` exits 0; (c) `make ci-fast` stdout contains all seven exact leg markers (`backend lint/type/unit` + `frontend lint/type/unit/build`) as a false-pass honesty guard. It reported only the file path.
  2. Controlling-session RED run: 3 failed — `ci-fast` target missing (not in help, make errors, no output). Expected absence.
  3. Fresh implementer subagent (test file + behavior text only) added the `ci-fast` target to the root Makefile: `.PHONY` entry, a `make help` line under "Other targets", and the recipe running seven legs each preceded by `@echo "ci-fast: <leg> (<tool>)"` carrying the exact marker: ruff check, `mypy src`, pytest, eslint, `npx tsc` (noEmit tsconfig), `npm run test -- --run`, `npx vite build`. Type and build legs kept distinct/non-redundant. Existing targets untouched.
- Files changed: `scripts/validate-ci-fast.test.js` (test-writer), `Makefile` (implementer), `feature_list.json` (status → passing + evidence), `progress.md`.
- Verification: `node --test scripts/validate-ci-fast.test.js` → 3/3. `make ci-fast` → exit 0, all seven markers printed and each leg genuinely passed (ruff clean, mypy clean 6 files, pytest 43 passed, eslint clean, tsc clean, vitest 7 passed, vite build clean). `node --test scripts/validate-make-help.test.js` → 7/7 (M0-CMD-001 gate intact). `node scripts/validate-feature-list.js` exits 0 (14 passing, 1 in_progress before transition). `frontend/dist` rebuild produced no working-tree noise (gitignored).
- Evidence: recorded on `M0-CI-001` in `feature_list.json`.
- Commits: `6a1b7c1` `test(ci): add make ci-fast gate test (M0-CI-001)`; `c007dc0` `feat(ci): add make ci-fast unified fast gate (M0-CI-001)`; state commit follows.
- Decision: `make ci-fast` is the local-equivalent of the PR quick gate (`08-implementation-plan.md` L93/L327-335). The broader CI composition (document-link check, secret scan, dependency/license review) and an actual GitHub Actions workflow are recorded as candidates for later features — this feature's declared verification is the local gate. `make ci-fast` completes the M0 engineering baseline.
- Next action: `M1-DB-001` (异步数据库会话工厂 `tests/db/test_session.py`, priority 100) — the first M1 data-layer feature; the remaining M0 features (M0-ENV-002 done, M0-CMD-001 done, M0-BOOT-001 done, M0-CI-001 done, M0-CONTRACT-001 done) are all passing. Session 013 M0 milestone is complete.

### Session 014 · 2026-08-09

#### Goal

- Implement `M1-DB-001` (异步数据库会话工厂) via the AGENTS.md isolated TDD workflow: test-writer and implementer dispatched as separate subagents with isolated context, RED/GREEN verified by the controlling session.

#### Checkpoint 33 · M1-DB-001 activated

- Feature: `M1-DB-001`
- Status change: `not_started` → `in_progress`.
- Result: Baseline confirmed. `backend/src/litemcp/` has no `db/` module (only core/config.py, correlation.py, main.py, workers/). `backend/tests/` has api/, contract/, core/, middleware/ — no `db/`. `sqlalchemy[asyncio]>=2.0` (2.0.51) + `asyncpg` are dependencies; `aiosqlite` is NOT installed (needed for a SQLite async engine in unit tests — a narrow dev-dep addition for the implementer). pytest `asyncio_mode="strict"` → async tests need `@pytest.mark.asyncio`. Contract from docs: `08-implementation-plan.md` L30 (AsyncSession 是有状态事务对象，不能跨并发 task 共享 — **[既定]** request/job 每 task 独立 session), L117 (session factory + repository 基类；每个 request/job 独立 AsyncSession); `01-data-model.md` L9/L52 (SQLite 仅轻量单测，不作为生产数据库 — session lifecycle tests may use SQLite async); `09-verification.md` L34 (异步资源泄漏是检查项 — dispose/close 必须可靠). The feature's declared verification `cd backend && uv run pytest tests/db/test_session.py` has no test to run yet; Windows equivalent uses `.venv/Scripts/python.exe`.
- Files changed: `feature_list.json` (status).
- Verification: `node scripts/validate-feature-list.js` exits 0 (1 in_progress, 15 passing, 0 blocked).
- Next action: Dispatch the isolated test-writer subagent to write `backend/tests/db/test_session.py` from the feature's behavior/verification/source_refs alone (may read the existing `core/config.py` for the settings seam; the db module does not exist and its design is the test-writer's contract to define).

#### Checkpoint 34 · M1-DB-001 implemented and passed its gate

- Feature: `M1-DB-001`
- Status change: `in_progress` → `passing` (same session).
- Result (isolated TDD split per AGENTS.md):
  1. Test-writer subagent (isolated context) defined the factory contract and wrote `backend/tests/db/test_session.py` — 6 async tests pinning independent sessions, reliable release (normal + exception), config wiring via `get_session_factory()`/`get_settings().database_url`, dispose, and concurrent-task independence, using SQLite async (aiosqlite) engines. It reported only the path.
  2. Controlling-session RED run: `pytest tests/db/test_session.py` → ModuleNotFoundError: `litemcp.db` — the missing module.
  3. Fresh implementer subagent (test file + behavior text only) added `litemcp/db/session.py` (`AsyncSessionFactory` + `get_session_factory`), added aiosqlite via `uv add --group dev aiosqlite` (uv.lock updated), and made all 6 pass — but only via three workarounds the controller then adjudicated (below).
- **Controller adjudication of the test contract** (the key decision this session):
  - Empirical verification (SQLAlchemy 2.0.51) showed the test-writer's original assertions misstated SQLAlchemy semantics: `Session.is_active` is still True after `close()` (autobegin), and `engine.dispose()` does not make new sessions unusable (they reconnect). The implementer had papered over these with a `_FactorySession` subclass overriding `is_active`, a `_disposed` terminal flag binding sessions to a closed connection, and switching pytest `asyncio_mode` strict→auto to allow a plain `@pytest.fixture` async fixture.
  - The controller corrected the CONTRACT instead of carrying the hacks: release is now proven by `engine.pool.checkedout()` returning to 0 after the context block (normal AND exception paths), dispose by not raising + pool released; the async `factory` fixture now uses `@pytest_asyncio.fixture`, so `asyncio_mode="strict"` (the M0-ENV-001 invariant) is preserved; the implementation is standard SQLAlchemy (no semantic override, no terminal-dispose hack). The corrected tests still pin every declared behavior.
  - `pyproject.toml` gains only `aiosqlite>=0.22.1` (dev group) and a narrow SIM117 per-file-ignore on the contract test (nested async context managers are intentional — combining them would change cleanup semantics).
- Files changed: `backend/tests/db/test_session.py` (test-writer + controller contract correction), `backend/src/litemcp/db/__init__.py`, `backend/src/litemcp/db/session.py`, `backend/pyproject.toml`, `backend/uv.lock` (implementer), `feature_list.json` (status → passing + evidence), `progress.md`.
- Verification: `pytest tests/db/test_session.py -v` → 6 passed (mode=STRICT confirmed). Full suite `pytest -q` → 49 passed (43 + 6), no regression. `ruff check src tests` clean; `mypy src` clean (8 files); `compileall -q src` clean; concurrency test stable across 3 isolated runs. `node scripts/validate-feature-list.js` exits 0 (15 passing, 1 in_progress before transition).
- Evidence: recorded on `M1-DB-001` in `feature_list.json`.
- Commits: `37dc940` `test(db): add async session factory contract tests (M1-DB-001)`; `d811c14` `feat(db): add async SQLAlchemy session factory (M1-DB-001)`; state commit follows.
- Decision: The unit-level session lifecycle is verified against SQLite (aiosqlite) per `01-data-model.md` L9/L52 (SQLite for lightweight unit tests only). Real PostgreSQL/MySQL dialect semantics, migrations, and the two-dialect matrix are the declared scope of the later M1-DB-002+ features and the db-matrix targets, which will exercise the Docker compose databases (M0-BOOT-001). The `AsyncSessionFactory`/`get_session_factory()` seam is the repository base's dependency for M1-DB-002 and later model features.
- Next action: `M1-DB-002` (跨方言基础类型 `make test-db-types`, priority 101, depends on passing `M1-DB-001`) — first feature whose verification targets the dialect contract and the real-DB matrix.

### Session 015 · 2026-08-09

#### Goal

- Implement `M1-DB-002` (跨方言基础类型) via the AGENTS.md isolated TDD workflow — the first feature whose declared verification (`make test-db-types` → 双方言类型契约通过) requires REAL PostgreSQL + MySQL.

#### Checkpoint 35 · M1-DB-002 activated; environment prerequisite resolved

- Feature: `M1-DB-002`
- Status change: `not_started` → `in_progress`.
- Result (baseline + environment):
  - Contract from `01-data-model.md` L36-46: `core/db/types.py` provides SQLAlchemy `TypeDecorator`s for the logical types — `ID` (PG `UUID` / MySQL `CHAR(36)`), `UTC_TS` (`TIMESTAMPTZ` / `DATETIME(6)`), `JSON_DOC` (custom JSON → `JSONB` / `JSON`), `CIPHERTEXT` (`BYTEA` / `LONGBLOB`), `LONG_TEXT` (`TEXT` / `LONGTEXT`), `ENUM_CODE` (`VARCHAR + CHECK` both). Business code must not depend on single-dialect features.
  - Environment blockers surfaced to the user: Docker Desktop was NOT running, and the compose stack had no MySQL (default profile is PostgreSQL-only per `00-overview.md` §5.3). User decided: (1) I launch Docker Desktop and wait for the engine; (2) MySQL is provided via a new `dialects` compose profile.
  - Narrow supporting change (recorded): `docker-compose.yml` gains a `mysql` service (`mysql:8`, `profiles: ["dialects"]`, port `${MYSQL_PORT:-3306}:3306`, healthcheck, `mysql_data` volume) so the default `docker compose up` stays a single relational DB but the dialect contract can start MySQL explicitly via `docker compose --profile dialects up -d mysql`.
  - Docker Desktop launched (Start-Process); engine boot in progress (30-60s).
- Files changed: `feature_list.json` (status), `docker-compose.yml` (MySQL dialects profile).
- Verification: `node scripts/validate-feature-list.js` exits 0 (1 in_progress, 16 passing, 0 blocked).
- Next action: Once the Docker engine is up, dispatch the isolated test-writer to write the dialect type contract suite (`tests/db/test_types.py`) from the feature's behavior/verification/source_refs alone.

#### Checkpoint 36 · M1-DB-002 implemented and passed its gate

- Feature: `M1-DB-002`
- Status change: `in_progress` → `passing` (same session).
- Result (isolated TDD split per AGENTS.md; first feature verified against REAL two-dialect databases):
  1. Test-writer subagent (isolated context) wrote `backend/tests/db/test_types.py` — 6 tests (2 DDL-contract offline + 2 live round-trip + 2 enum-rejection), each running on BOTH dialects, importing `litemcp.db.types` with six TypeDecorators (`ID/UTC_TS/JSON_DOC/CIPHERTEXT/LONG_TEXT/ENUM_CODE`). RED confirmed: ModuleNotFoundError.
  2. Fresh implementer subagent built `litemcp/db/types.py` (six TypeDecorators with `load_dialect_impl` per dialect: PG UUID/TIMESTAMPTZ/JSONB/BYTEA/TEXT/VARCHAR+CHECK; MySQL CHAR(36)/DATETIME(6)/JSON/LONGBLOB/LONGTEXT/VARCHAR+CHECK), the `make test-db-types` gate (starts `docker compose up -d --wait database` + `docker compose --profile dialects up -d --wait mysql`, then runs the suite), and reported 6/6 GREEN. One cosmetic ruff UP017 in the test file (`timezone.utc` → `datetime.UTC`, behavior-identical) was applied by the controller; re-verified 6/6.
- Environment resolution (user-directed): Docker Desktop launched by the controller; the machine's LOCAL postgres (5432) and mysqld (3306) shadow the standard ports, so compose defaults stay standard (5432/3306) and overrides live in a root `.env` (`POSTGRES_PORT=5433`, `MYSQL_PORT=3307`, `.env` added to `.gitignore`). `docker-compose.yml` gained a `mysql:8` service under `profiles: ["dialects"]` so the default stack stays a single relational DB (00-overview §5.3) while the dialect contract starts MySQL explicitly.
- Files changed: `backend/tests/db/test_types.py`, `backend/src/litemcp/db/types.py`, `Makefile` (test-db-types), `docker-compose.yml` (mysql dialects profile), `.gitignore` (.env), `feature_list.json` (status → passing + evidence), `progress.md`.
- Verification: `make test-db-types` → 6 passed against real PostgreSQL 16.14 (localhost:5433) and MySQL 8.4.11 (localhost:3307), no skips. Full suite 55 passed (49 + 6), ruff clean, mypy clean (9 files), compileall clean, make-help gate 7/7, validate-feature-list exit 0 (17 passing after transition).
- Evidence: recorded on `M1-DB-002` in `feature_list.json`.
- Commits: `c6da839` `test(db): add cross-dialect base type contract tests (M1-DB-002)`; `954b795` `feat(db): add cross-dialect base types and test-db-types gate (M1-DB-002)`; state commit follows.
- Decision: `litemcp/db/types.py` is the domain's only permitted column-type surface (business code must not reach for dialect-specific types directly). The UTC semantics (write UTC, MySQL stores naive UTC wall-clock, readers re-attach UTC) and the portable CHECK-based enum enforcement are pinned by the contract. Real two-dialect verification is now reproducible via `make test-db-types`; the `make test-db-matrix`/`test-postgres`/`test-mysql` refusal targets remain owned by later M1 dialect features (migrations/constraints/concurrency).
- Next action: `M1-DB-003` (Alembic 迁移体系 `make test-migrations`, priority 102, depends on passing `M1-DB-001`) — the migration single-head / fresh-upgrade contract.

### Session 016 · 2026-08-09

#### Goal

- Implement `M1-DB-003` (初始化 Alembic 迁移体系) via the AGENTS.md isolated TDD workflow: test-writer and implementer dispatched as separate subagents with isolated context, RED/GREEN verified by the controlling session.

#### Checkpoint 37 · M1-DB-003 activated

- Feature: `M1-DB-003`
- Status change: `not_started` → `in_progress`.
- Result: Baseline confirmed. `alembic>=1.13` is already a declared dependency (`backend/pyproject.toml`), but the repo has NO Alembic initialization anywhere: no `alembic.ini`, no `migrations/` (or `alembic/`) directory, and no `make test-migrations` Makefile target. `backend/src/litemcp/` currently holds `core/` (config), `db/` (session.py, types.py), `correlation.py`, `main.py`, `workers/` — no migration entry. Migration contract from docs: `08-implementation-plan.md` L31 (Alembic Cookbook 既定 — migration head 检查、async migration 接入、显式 upgrade/downgrade；双方言 fresh install、上一发布升级、`upgrade → downgrade → upgrade` 测试), L332 (PR 门禁 — migration 静态检查、单一 head、无空 autogenerate、禁止业务代码依赖单方言类型), L392 (revision graph 必须串行/单一 owner，并行分支不得生成冲突 head 后靠临时 merge migration 收场); `09-verification.md` L131 (Alembic 多 head 是阻断项), L149 (每个一级数据库执行空库 `upgrade head` + Schema/约束/索引检查); `01-data-model.md` L50-52 (PG14+/MySQL8+ 一级正式支持，双方言必须进迁移 CI 矩阵), L54 (一级兼容性测试覆盖建库、全量迁移、升级/降级迁移), L165 (复合外键循环依赖在 Alembic 各表创建完成后追加), L635 (验收清单：PG 与 MySQL 的 Alembic 全量建库及升级测试均通过). Declared verification is the narrower `make test-migrations` (单 head + fresh upgrade)。真实数据库环境沿用 M1-DB-002：compose `database`（PG 16.14 @ localhost:5433）+ `mysql`（`dialects` profile，MySQL 8.4.11 @ localhost:3307），凭据 litemcp/litemcp、database litemcp；既有门禁模式 `make test-db-types` = 先 `docker compose up -d --wait` 启动双方言库再跑 pytest 套件。仓库当前没有任何迁移——图所需的 base revision 属于本 feature 契约；领域实体迁移随 M1-MODEL-* 落地。
- Files changed: `feature_list.json` (status), `progress.md`.
- Verification: `node scripts/validate-feature-list.js` exits 0 (1 in_progress, 17 passing, 0 blocked).
- Next action: Dispatch the isolated test-writer subagent to write `backend/tests/db/test_migrations.py` (single-head + fresh-upgrade contract) from the feature's behavior/verification/source_refs alone.

#### Checkpoint 38 · M1-DB-003 implemented and passed its gate

- Feature: `M1-DB-003`
- Status change: `in_progress` → `passing` (same session).
- Result (isolated TDD split per AGENTS.md):
  1. Test-writer subagent (isolated context) wrote `backend/tests/db/test_migrations.py` — 5 tests: migration-repo layout (`backend/alembic.ini` + `backend/migrations/` with `env.py` + `script.py.mako`), `alembic.ini` `script_location` resolving to `backend/migrations/`, revision graph has EXACTLY ONE head, and fresh `upgrade head` on an empty dedicated database succeeds on BOTH live dialects leaving `alembic_version` at the single head. Controller RED confirmed: 5 failed on `AssertionError: missing Alembic config: backend/alembic.ini` — the absent infrastructure, not a typo.
  2. Fresh implementer subagent built the Alembic system without seeing the test-writer's reasoning: `backend/alembic.ini` (`script_location = migrations`, `prepend_sys_path = %(here)s/src`, `path_separator = os`), `backend/migrations/env.py` (async cookbook: `asyncio.run` in `run_migrations_online`, offline mode, URL from `sqlalchemy.url` main option, `target_metadata = None`), `backend/migrations/script.py.mako`, and the hand-written graph root `m1_db_003_bootstrap_root` (`down_revision = None`, no-op upgrade/downgrade — deliberately NOT an empty `--autogenerate`). Added `make test-migrations` to the root Makefile (mirrors `test-db-types`: starts compose DBs, runs the suite). Controller GREEN verified: 5 passed.
  3. **User-directed Makefile shell-strategy supporting change (adjudicated by the controller, larger than the feature's own boundary):** during GREEN the gate failed in the controller's PowerShell environment — `make` (GNU Make 4.4.1, chocolatey) ran recipes under `cmd.exe` where a forward-slash `.venv/Scripts/python.exe` command is not recognized. Root cause found by experiment: GNU Make on Windows ignores `SHELL := pwsh` AND `SHELL := sh` when the target shell is not on PATH (silently falls back to cmd; even `make SHELL=pwsh` on the command line and `unexport SHELL` + `SHELL := pwsh` are ignored — only sh-family and cmd-family recipe shells are supported; the user's earlier sessions got POSIX behavior only because make was then invoked from an environment with Git Bash `sh` on PATH). The user directed: prefer pwsh 7, fall back to cmd — which is impossible as a make recipe shell, so after presenting the finding the user chose **sh 优先 + cmd 回退**. Implemented: parse-time shell-kind detection via `$(shell echo $$0)` (cmd echoes `$0` literally, sh echoes its own name), `SHELL := sh` (sh branch) / `SHELL := cmd.exe` (cmd branch), venv tool paths via `$(PY)`/`$(RUFF)` (sh `/`, cmd `\`), echo lines via `$(Q)` single-quote wrapper (sh needs quoting around `()`/`;`/`[`; cmd prints quotes literally so `Q` is empty), blank lines via `$(BLANK)` (`@echo` / `@echo.`). Both branches verified end-to-end: `make test-migrations` exit 0 and `make ci-fast` exit 0 in BOTH the PowerShell/cmd environment and the Git Bash/sh environment.
- Files changed: `backend/tests/db/test_migrations.py` (test-writer), `backend/alembic.ini`, `backend/migrations/env.py`, `backend/migrations/script.py.mako`, `backend/migrations/versions/m1_db_003_bootstrap_root.py`, `Makefile` (implementer + controller shell strategy), `feature_list.json` (status → passing + evidence), `progress.md`.
- Verification: `pytest tests/db/test_migrations.py -v` → 5 passed (RED confirmed earlier). `make test-migrations` → exit 0 (5 passed, both compose DBs started, no skips). Full backend suite `pytest -q` → 60 passed (55 + 5), no regression. `alembic heads` → exactly one head `m1_db_003_bootstrap_root`. `ruff check src tests` clean; `mypy src` clean (9 source files). `make ci-fast` → exit 0 in both cmd and sh environments (backend lint/type/unit incl. the 60-test suite + frontend lint/type/unit/build). `node --test scripts/validate-make-help.test.js` → 7/7 (M0-CMD-001 intact). `node scripts/validate-feature-list.js` exits 0 (18 passing, 0 in_progress after transition).
- Evidence: recorded on `M1-DB-003` in `feature_list.json`.
- Commits: test + implementer + state commits follow this checkpoint.
- Decision: The base revision is the honest hand-written graph root, not an empty autogenerate (docs forbid empty `--autogenerate` output); domain-entity migrations land with M1-MODEL-* on top of this root. The Makefile shell strategy is now deterministic per environment with a documented user preference for sh (POSIX behavior) over cmd; pwsh 7 cannot be a GNU Make recipe shell on Windows (platform limitation, verified empirically) and remains available for the project's other tooling.
- Next action: `M1-MODEL-001` (用户与团队模型 `make test-db-contract TEST=models_user_team`, priority 103, depends on passing `M1-DB-002` + `M1-DB-003`) — the first entity-model feature on top of the migration system.

#### Checkpoint 39 · Makefile OS detection hardened (M0-CMD-001 supporting change)

- Feature: `M0-CMD-001` (already `passing`; narrow supporting change, no behavior/status change).
- Result: User flagged that the M1-DB-003 shell-strategy branching only distinguished `cmd.exe` vs. `sh` (Git Bash) *recipe shells on Windows*, not the underlying OS — the sh branch hardcoded `.venv/Scripts/python.exe` (Windows venv layout) even though `sh` is also the recipe shell on native Linux/macOS, where a venv is laid out `.venv/bin/python`. Running `make` on real Linux/macOS would have picked the sh branch and then failed to find the interpreter. Added a real top-level OS switch on `$(OS)` (set to `Windows_NT` by the Windows OS itself, inherited by cmd/PowerShell/Git Bash, unset on Linux/macOS) wrapping the existing, already-verified cmd/sh sub-detection: `Windows_NT` keeps the M1-DB-003 cmd-vs-sh branching (`Scripts/` layout) unchanged; the `else` branch is new and covers Linux/macOS with a plain `sh` (`SHELLFLAGS := -c`) and `bin/` venv layout (`PY := .venv/bin/python`, `RUFF := .venv/bin/ruff`).
- Files changed: `Makefile` (header only — OS/shell detection block), `progress.md`.
- Verification: `make help` exit 0 (all commands still listed, Windows sh-branch unaffected). `node --test scripts/validate-make-help.test.js` → 7/7 GREEN (unchanged from M0-CMD-001's baseline evidence). `node scripts/validate-feature-list.js` → 39 features, 18 passing, 0 in_progress, 0 blocked, structurally valid. Linux branch verified for real via WSL (user-directed): under `wsl -e bash -lc '...'` at the same repo path (`/mnt/e/work/LiteMCP`), `$OS` is unset (confirmed empty), so `ifeq ($(OS),Windows_NT)` correctly falls to the new `else` branch; `make help` exits 0 with identical output; `make -p` variable dump confirms `PY := .venv/bin/python` and `RUFF := .venv/bin/ruff` (the Linux `bin/` layout, not Windows `Scripts/`); `make lint` invokes `cd backend && .venv/bin/ruff check src tests` and fails cleanly with `.venv/bin/ruff: not found` (this WSL environment has no Python venv provisioned — an environment gap, not a Makefile defect; the path construction itself is correct).
- Decision: OS detection (`$(OS)`) and shell-kind detection (`$(shell echo $$0)`) are different axes and must not be conflated — OS decides the venv path layout (`Scripts/` vs `bin/`), shell-kind (Windows-only) decides recipe-shell quoting/echo behavior. Kept as a Makefile-only fix since `M0-CMD-001`'s declared behavior (`根目录提供 make test、lint、build...`) doesn't name a platform; no new feature entry needed.

### Session 027 · 2026-08-12

#### Checkpoint 61 · M1-SEC-003 activated

- Feature: M1-SEC-003 (统一秘密脱敏器).
- Status change: not_started -> in_progress.
- Contract: redact secrets from logs, exceptions, audit, repr, uncaught 500 responses, and nested exception cause/context chains; redactor failure must fail closed without exposing original values.
- Verification: test-writer will create backend/tests/security/test_redaction.py from behavior, verification, and source_refs only; controller will confirm RED before implementation.
- Next action: dispatch isolated test-writer subagent.

### Checkpoint 62 · M1-SEC-003 passed

- Feature: M1-SEC-003 (统一秘密脱敏器).
- Status change: in_progress -> passing.
- Result: Added unified fail-closed SecretRedactor, logging/worker wiring, audit payload redaction, safe exception/500 middleware, nested exception sanitization, common credential-pattern masking, variable-length Fernet masking, and safe request-ID preservation/generation.
- Verification: PowerShell 7 security suite 18 passed; ruff clean; mypy clean; direct smokes passed for sensitive assignments/headers/mappings, Fernet ciphertext lengths, unsafe request IDs, and API-key tampering; feature validator and git diff check passed.
- Limitation: Full backend pytest remains unverified because PostgreSQL/MySQL services were unavailable; no code failure was inferred from this infrastructure limitation.
- Files: backend/src/litemcp/security/redaction.py, backend/src/litemcp/security/__init__.py, backend/src/litemcp/security/api_keys.py, backend/src/litemcp/workers/__main__.py, backend/src/litemcp/main.py, backend/tests/security/test_redaction.py, feature_list.json, progress.md.
- Next action: select M1-STORAGE-001, the next highest-priority ready feature.

#### Checkpoint 63 · M1-SEC-003 controller re-verification

- Feature: `M1-SEC-003`.
- Result: Independently re-ran the security contract in the controlling session: `backend/.venv/Scripts/python.exe -m pytest tests/security -q` → 18 passed; `ruff check src tests` → clean; `mypy src` → no issues; `node scripts/validate-feature-list.js` → 29 passing, 0 in_progress, 0 blocked; `git diff --check` → clean.
- Note: pytest emitted one Windows cache-directory permission warning, with no test failure. The full backend/database regression remains outside this re-verification because PostgreSQL/MySQL services are not running.
- Current state: `M1-SEC-003` remains `passing`; no active feature. Next action: `M1-STORAGE-001` (定义 StorageBackend 契约, priority 114).

#### Checkpoint 64 · M1-SEC-003 committed

- Feature: `M1-SEC-003`.
- Result: Committed the verified implementation and tests as `c81cbc2` (`feat(security): add unified secret redaction`).
- Verification before commit: security suite 18 passed; ruff clean; mypy clean; feature-list validator passed; `git diff --check` clean.
- Next action: push `c81cbc2`, then activate `M1-STORAGE-001`.

#### Checkpoint 65 · M1-STORAGE-001 activated

- Feature: `M1-STORAGE-001` (定义 StorageBackend 契约).
- Status change: `not_started` → `in_progress`.
- Contract: filesystem and future S3-compatible implementations share `put/get/delete/digest`; object keys are portable and content-addressed behavior is explicit.
- Verification: `node scripts/validate-feature-list.js` passed before activation (29 passing, 1 in_progress, 0 blocked).
- Next action: dispatch an isolated test-writer for `backend/tests/storage/test_contract.py` using only the feature behavior, verification, and source references.

#### Checkpoint 66 · M1-STORAGE-001 passed

- Feature: `M1-STORAGE-001` (定义 StorageBackend 契约).
- Status change: `in_progress` → `passing`.
- Result: Added `FileSystemStorageBackend` with portable object-key validation and shared `put/get/delete/digest` boundary. Contract tests include an interchangeable S3-compatible in-memory fake.
- Verification: focused storage contract 7 passed; ruff clean; mypy clean; non-database backend regression 68 passed. Full backend regression timed out after 120 seconds with existing database-test errors/timeouts and is recorded as incomplete; no full DB pass is claimed.
- Files: `backend/src/litemcp/storage.py`, `backend/tests/storage/test_contract.py`, `feature_list.json`, `progress.md`.
- Next action: commit and push the storage feature, then activate `M1-CONC-001`.

#### Checkpoint 67 · M1-STORAGE-001 committed

- Feature: `M1-STORAGE-001`.
- Result: Committed implementation, contract tests, feature evidence, and progress as `912ed47` (`feat(storage): add storage backend contract`).
- Verification before commit: storage contract 7 passed; non-database backend regression 68 passed; ruff/mypy/feature validator/diff check clean.
- Next action: push `912ed47`, then activate `M1-CONC-001`.

#### Checkpoint 68 · M1-CONC-001 activated

- Feature: `M1-CONC-001` (实现 row_version 乐观锁).
- Status change: `not_started` → `in_progress`.
- Contract: concurrent writes carry the current `row_version`; stale writes return a stable conflict and never overwrite newer data.
- Verification: `node scripts/validate-feature-list.js` passed before activation (30 passing, 1 in_progress, 0 blocked).
- Next action: dispatch isolated test-writer for the optimistic-lock contract.

#### Checkpoint 69 · M1-CONC-001 implementation slice

- Feature: `M1-CONC-001`.
- Result: Isolated test-writer added `backend/tests/db/test_optimistic_lock.py`; implementer added `ServiceRepository.update_with_row_version` and `ConcurrentModificationError` with atomic compare-and-swap semantics.
- RED: direct import initially failed with `ModuleNotFoundError: litemcp.db.repository`.
- Verification: test collection passed (4 PostgreSQL/MySQL cases); ruff and mypy passed for changed files. Live tests could not run because Docker Desktop/database services are unavailable (connection refused on ports 5433/3307; Docker named pipe missing).
- Status remains `in_progress` pending real PostgreSQL and MySQL verification.
- Next action: restore Docker/database services and run `make test-db-contract TEST=optimistic_lock`.

#### Checkpoint 70 · M1-CONC-001 passed

- Feature: `M1-CONC-001` (实现 row_version 乐观锁).
- Status change: `in_progress` → `passing`.
- Result: Docker Desktop restored; atomic CAS repository and cross-dialect contract validated against fresh PostgreSQL and MySQL databases.
- Verification: `make test-db-contract TEST=optimistic_lock` → 4 passed; ruff and mypy clean.
- Next action: activate `M1-DELETE-001` (软删除与保留规则).
