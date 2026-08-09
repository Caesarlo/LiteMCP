# LiteMCP root entry point.
#
# Unified management entry (M0-CMD-001): `make help` documents test, lint, build,
# test-postgres, test-mysql and test-db-matrix. `validate-env-example` is
# contributed by M0-ENV-002. The frontend test leg joins `make test` once
# M0-FE-001 establishes the frontend test scaffold.
#
# Environment: Windows (GNU Make 4.4.1 via chocolatey). Backend tools are invoked
# through the local venv (uv is not available in WSL bash); node/npm/make are on PATH.

.PHONY: help test lint build test-postgres test-mysql test-db-matrix validate-env-example

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
	@echo "Other targets:"
	@echo "  make validate-env-example   gate .env.example coverage and no real secrets (M0-ENV-002)"

test:
	cd backend && .venv/Scripts/python.exe -m pytest
	@echo "[make test] frontend test leg not present yet; added with M0-FE-001 (skipped)."

lint:
	cd backend && .venv/Scripts/ruff.exe check src tests
	cd frontend && npm run lint

build:
	cd backend && .venv/Scripts/python.exe -m compileall -q src
	cd frontend && npm run build

test-postgres:
	@echo "[make test-postgres] PostgreSQL dialect contract matrix is not available yet: requires M0-BOOT-001 (compose) and M1-DB-* (dialect types/contract tests). Refusing to false-pass."
	@exit 1

test-mysql:
	@echo "[make test-mysql] MySQL dialect contract matrix is not available yet: requires M0-BOOT-001 (compose) and M1-DB-* (dialect types/contract tests). Refusing to false-pass."
	@exit 1

test-db-matrix:
	@echo "[make test-db-matrix] Full two-dialect matrix is not available yet: requires M0-BOOT-001 (compose) and M1-DB-* (dialect types/contract tests). Refusing to false-pass."
	@exit 1

validate-env-example:
	node scripts/validate-env-example.js
