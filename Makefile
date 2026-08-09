# LiteMCP root entry point.
#
# Minimal for now: M0-ENV-002 contributes `validate-env-example`. The full
# command set (test, lint, build, test-postgres, test-mysql, test-db-matrix)
# is added by feature M0-CMD-001.

.PHONY: validate-env-example

validate-env-example:
	node scripts/validate-env-example.js
