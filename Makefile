# LiteMCP root entry point.
#
# Unified management entry (M0-CMD-001): `make help` documents test, lint, build,
# test-postgres, test-mysql and test-db-matrix. `validate-env-example` is
# contributed by M0-ENV-002. The frontend test leg joins `make test` once
# M0-FE-001 establishes the frontend test scaffold.
#
# Environment: Windows (GNU Make 4.4.1 via chocolatey). Backend tools are invoked
# through the local venv; node/npm/make are on PATH.
#
# Shell strategy (user-directed, M1-DB-003): prefer a POSIX sh when one is
# available (e.g. running make from Git Bash), else fall back to cmd.exe. GNU Make
# on Windows only supports sh-family or cmd-family recipe shells (SHELL := pwsh is
# silently ignored), and a recipe shell decides both the venv path separator
# (sh: `/`, cmd: `\`) and echo quoting (sh strips quotes and must quote parens/
# semicolons; cmd prints quotes literally). Shell kind is detected at parse time
# with `$(shell echo $$0)` — cmd echoes `$0` literally, sh echoes its own name.
DEFAULT_SHELL := $(shell echo $$0)
ifeq ($(DEFAULT_SHELL),$$0)
  SHELL := cmd.exe
  PY := .venv\Scripts\python.exe
  RUFF := .venv\Scripts\ruff.exe
  Q :=
  BLANK := @echo.
  SHELLKIND := cmd
else
  SHELL := sh
  SHELLFLAGS := -c
  PY := .venv/Scripts/python.exe
  RUFF := .venv/Scripts/ruff.exe
  Q := '
  BLANK := @echo
  SHELLKIND := sh
endif

.PHONY: help test lint build test-postgres test-mysql test-db-matrix test-db-types test-migrations validate-env-example validate-adr test-openapi update-openapi-snapshot ci-fast

help:
	@echo $(Q)LiteMCP unified management entry point.$(Q)
	$(BLANK)
	@echo $(Q)Unified commands (M0-CMD-001):$(Q)
	@echo $(Q)  make test             backend unit/integration tests (frontend test leg added by M0-FE-001)$(Q)
	@echo $(Q)  make lint             backend ruff + frontend eslint$(Q)
	@echo $(Q)  make build            backend compile check + frontend tsc/vite build$(Q)
	@echo $(Q)  make test-postgres    PostgreSQL dialect contract tests (needs M0-BOOT-001 compose + M1 dialect)$(Q)
	@echo $(Q)  make test-mysql       MySQL dialect contract tests (needs M0-BOOT-001 compose + M1 dialect)$(Q)
	@echo $(Q)  make test-db-matrix   full two-dialect matrix (needs M0-BOOT-001 compose + M1 dialect)$(Q)
	$(BLANK)
	@echo $(Q)Dialect contract:$(Q)
	@echo $(Q)  make test-db-types     M1-DB-002 cross-dialect type contract on live PostgreSQL + MySQL$(Q)
	@echo $(Q)  make test-migrations   M1-DB-003 Alembic single-head + fresh upgrade on live PostgreSQL + MySQL$(Q)
	$(BLANK)
	@echo $(Q)Other targets:$(Q)
	@echo $(Q)  make ci-fast                 run all seven fast CI legs: backend lint/type/unit + frontend lint/type/unit/build (M0-CI-001)$(Q)
	@echo $(Q)  make test-openapi            gate the committed OpenAPI snapshot against the live spec (M0-CONTRACT-001)$(Q)
	@echo $(Q)  make update-openapi-snapshot regenerate and commit the OpenAPI snapshot after an intended contract change (M0-CONTRACT-001)$(Q)
	@echo $(Q)  make validate-env-example   gate .env.example coverage and no real secrets (M0-ENV-002)$(Q)
	@echo $(Q)  make validate-adr           gate docs/adr/ structure and required M0 topic coverage (M0-ADR-001)$(Q)

test:
	cd backend && $(PY) -m pytest
	@echo $(Q)[make test] frontend test leg not present yet; added with M0-FE-001 (skipped).$(Q)

lint:
	cd backend && $(RUFF) check src tests
	cd frontend && npm run lint

build:
	cd backend && $(PY) -m compileall -q src
	cd frontend && npm run build

ci-fast:
	@echo $(Q)ci-fast: backend lint (ruff)$(Q)
	cd backend && $(RUFF) check src tests
	@echo $(Q)ci-fast: backend type (mypy)$(Q)
	cd backend && $(PY) -m mypy src
	@echo $(Q)ci-fast: backend unit (pytest)$(Q)
	cd backend && $(PY) -m pytest
	@echo $(Q)ci-fast: frontend lint (eslint)$(Q)
	cd frontend && npm run lint
	@echo $(Q)ci-fast: frontend type (tsc)$(Q)
	cd frontend && npx tsc
	@echo $(Q)ci-fast: frontend unit (vitest)$(Q)
	cd frontend && npm run test -- --run
	@echo $(Q)ci-fast: frontend build (vite)$(Q)
	cd frontend && npx vite build

test-postgres:
	@echo $(Q)[make test-postgres] PostgreSQL dialect contract matrix is not available yet: requires M0-BOOT-001 (compose) and M1-DB-* (dialect types/contract tests). Refusing to false-pass.$(Q)
	@exit 1

test-mysql:
	@echo $(Q)[make test-mysql] MySQL dialect contract matrix is not available yet: requires M0-BOOT-001 (compose) and M1-DB-* (dialect types/contract tests). Refusing to false-pass.$(Q)
	@exit 1

test-db-matrix:
	@echo $(Q)[make test-db-matrix] Full two-dialect matrix is not available yet: requires M0-BOOT-001 (compose) and M1-DB-* (dialect types/contract tests). Refusing to false-pass.$(Q)
	@exit 1

test-db-types:
	docker compose up -d --wait database
	docker compose --profile dialects up -d --wait mysql
	cd backend && $(PY) -m pytest tests/db/test_types.py -q

test-migrations:
	docker compose up -d --wait database
	docker compose --profile dialects up -d --wait mysql
	cd backend && $(PY) -m pytest tests/db/test_migrations.py -q

validate-env-example:
	node scripts/validate-env-example.js

validate-adr:
	node scripts/validate-adr.js

test-openapi:
	cd backend && $(PY) -m pytest tests/contract/test_openapi_snapshot.py -q

update-openapi-snapshot:
	cd backend && $(PY) scripts/regenerate_openapi.py
