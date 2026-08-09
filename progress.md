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

- Last updated: 2026-08-09
- Repository root: `E:\work\LiteMCP`
- Active feature: None.
- Standard startup: Three paths now coexist. (1) Separate commands documented in `README.zh-CN.md`. (2) Root `Makefile` unified entry (`make help`, `test`, `lint`, `build`, `test-postgres`, `test-mysql`, `test-db-matrix` — `M0-CMD-001`) alongside `validate-env-example` (`M0-ENV-002`). (3) Compose orchestration (`M0-BOOT-001`): root `docker-compose.yml` starts database/redis/backend/worker/frontend via `docker compose up -d` (default PostgreSQL, ports 8000/5173/5432/6379; all `${VAR}` carry inline defaults so it parses without `.env`). Before any implementation work, run `node scripts/validate-feature-list.js` (Windows node; WSL bash lacks node/uv) and (once per clone) `git config core.hooksPath .githooks` — already set in this clone.
- Standard verification: `make test` runs backend unit/integration tests (frontend test leg lands with `M0-FE-001`); `make lint` runs backend ruff + frontend eslint; `make build` runs backend compileall + frontend tsc/vite build. The db-matrix targets (`test-postgres`/`test-mysql`/`test-db-matrix`) currently refuse to false-pass (exit non-zero with a prerequisite notice) until `M0-BOOT-001` compose + `M1-DB-*` dialect contracts exist; their real verification is declared by the corresponding M1 features. Windows-equivalent for backend tests: `cd backend && .venv/Scripts/python.exe -m pytest ...` (uv unavailable in WSL bash). `node scripts/validate-feature-list.js` is the repeatable structural/pass-gate check for `feature_list.json` itself; `make validate-env-example` (node, Windows-OK) gates `.env.example` coverage and no-real-secrets; `make validate-adr` (node, Windows-OK) gates `docs/adr/` structure and the 6 required M0 topic coverage.
- Current blocker: None.
- Last passing feature: `M0-BE-003` (backend correlation-id middleware; `tests/middleware/test_correlation.py` — 16 tests pin ingress `X-Request-ID` validation `[A-Za-z0-9._-]+` 1–128 bytes with regeneration on invalid/absent, uniform `X-Request-Id` response echo incl. 404, and context propagation via `request.state.request_id`; RED observed in-session, isolated TDD split exercised end-to-end; implementer added `correlation.py` `CorrelationIdMiddleware` mounted in `main.py`, `/livez`/`/readyz` unchanged. Previous: `M0-BE-002` `/readyz` 3 contract tests + real fail-closed DB/Redis probes; `M0-BE-001` `/livez` 3 contract tests).

## Session Log

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
