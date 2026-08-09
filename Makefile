# LiteMCP root entry point.
#
# Unified management entry (M0-CMD-001): `make help` documents test, lint, build,
# test-postgres, test-mysql and test-db-matrix. `validate-env-example` is
# contributed by M0-ENV-002. The frontend test leg joins `make test` once
# M0-FE-001 establishes the frontend test scaffold.
#
# Environment: Windows (GNU Make 4.4.1 via chocolatey). Backend tools are invoked
# through the local venv (uv is not available in WSL bash); node/npm/make are on PATH.

.PHONY: help test lint build test-postgres test-mysql test-db-matrix test-db-types validate-env-example validate-adr test-openapi update-openapi-snapshot ci-fast

help:
	@echo "LiteMCP unified management entry point."
	@echo ""
	@echo "Unified commands (M0-CMD-001):"
	@echo "  make test             backend unit/integration tests (frontend test leg added by M0-FE-001)"
	@echo "  make lint             backend ruff + frontend eslint"
	@echo "  make build            backend compile check + frontend tsc/vite build"
	@echo "  make test-postgres    PostgreSQL dialect contract tests (needs M0-BOOT-001 compose + M1 dialect)"
	@echo "  make test-mysql       MySQL dialect contract tests (needs M0-BOOT-001 compose + M1 dialect)"
	@echo "  make test-db-matrix   full two-dialect matrix (needs M0-BOOT-001 compose + M1 dialect)"
	@echo ""
	@echo "Dialect contract:"
	@echo "  make test-db-types     M1-DB-002 cross-dialect type contract on live PostgreSQL + MySQL"
	@echo ""
	@echo "Other targets:"
	@echo "  make ci-fast                 run all seven fast CI legs: backend lint/type/unit + frontend lint/type/unit/build (M0-CI-001)"
	@echo "  make test-openapi            gate the committed OpenAPI snapshot against the live spec (M0-CONTRACT-001)"
	@echo "  make update-openapi-snapshot regenerate and commit the OpenAPI snapshot after an intended contract change (M0-CONTRACT-001)"
	@echo "  make validate-env-example   gate .env.example coverage and no real secrets (M0-ENV-002)"
	@echo "  make validate-adr           gate docs/adr/ structure and required M0 topic coverage (M0-ADR-001)"

test:
	cd backend && .venv/Scripts/python.exe -m pytest
	@echo "[make test] frontend test leg not present yet; added with M0-FE-001 (skipped)."

lint:
	cd backend && .venv/Scripts/ruff.exe check src tests
	cd frontend && npm run lint

build:
	cd backend && .venv/Scripts/python.exe -m compileall -q src
	cd frontend && npm run build

ci-fast:
	@echo "ci-fast: backend lint (ruff)"
	cd backend && .venv/Scripts/ruff.exe check src tests
	@echo "ci-fast: backend type (mypy)"
	cd backend && .venv/Scripts/python.exe -m mypy src
	@echo "ci-fast: backend unit (pytest)"
	cd backend && .venv/Scripts/python.exe -m pytest
	@echo "ci-fast: frontend lint (eslint)"
	cd frontend && npm run lint
	@echo "ci-fast: frontend type (tsc)"
	cd frontend && npx tsc
	@echo "ci-fast: frontend unit (vitest)"
	cd frontend && npm run test -- --run
	@echo "ci-fast: frontend build (vite)"
	cd frontend && npx vite build

test-postgres:
	@echo "[make test-postgres] PostgreSQL dialect contract matrix is not available yet: requires M0-BOOT-001 (compose) and M1-DB-* (dialect types/contract tests). Refusing to false-pass."
	@exit 1

test-mysql:
	@echo "[make test-mysql] MySQL dialect contract matrix is not available yet: requires M0-BOOT-001 (compose) and M1-DB-* (dialect types/contract tests). Refusing to false-pass."
	@exit 1

test-db-matrix:
	@echo "[make test-db-matrix] Full two-dialect matrix is not available yet: requires M0-BOOT-001 (compose) and M1-DB-* (dialect types/contract tests). Refusing to false-pass."
	@exit 1

test-db-types:
	docker compose up -d --wait database
	docker compose --profile dialects up -d --wait mysql
	cd backend && .venv/Scripts/python.exe -m pytest tests/db/test_types.py -q

validate-env-example:
	node scripts/validate-env-example.js

validate-adr:
	node scripts/validate-adr.js

test-openapi:
	cd backend && .venv/Scripts/python.exe -m pytest tests/contract/test_openapi_snapshot.py -q

update-openapi-snapshot:
	cd backend && .venv/Scripts/python.exe scripts/regenerate_openapi.py
