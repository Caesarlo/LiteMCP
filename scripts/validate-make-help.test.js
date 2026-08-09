// Automated gate for M0-CMD-001 (root Makefile unified entry).
// Dependency-free, mirrors the node:test style of scripts/validate-env-example.test.js.
// The declared feature verification is `make help`; this gate also checks that the
// implemented legs behave honestly: test/lint/build run for real, while the db-matrix
// trio refuses to false-pass until its dialect prerequisites (M0-BOOT-001 compose,
// M1-DB-* dialect contracts) exist.
const { execFileSync } = require('node:child_process');
const test = require('node:test');
const assert = require('node:assert/strict');

const REQUIRED_TARGETS = ['test', 'lint', 'build', 'test-postgres', 'test-mysql', 'test-db-matrix'];
const PREREQUISITE_NOTE = /not available|not yet|prerequisite/i;

function runMake(target) {
  try {
    const stdout = execFileSync('make', [target], { encoding: 'utf8' });
    return { status: 0, stdout };
  } catch (err) {
    return { status: err.status ?? 1, stdout: String(err.stdout ?? '') };
  }
}

test('make help lists every M0-CMD-001 unified command', () => {
  const { status, stdout } = runMake('help');
  assert.equal(status, 0, `make help should succeed, got exit ${status}:\n${stdout}`);
  const listed = new Set();
  for (const line of stdout.split(/\r?\n/)) {
    const m = line.match(/^\s*make\s+([A-Za-z0-9_-]+)/);
    if (m) listed.add(m[1]);
  }
  for (const target of REQUIRED_TARGETS) {
    assert.ok(listed.has(target), `make help must list target '${target}'`);
  }
});

test('make test runs the implemented test legs', () => {
  const { status, stdout } = runMake('test');
  assert.equal(status, 0, `make test should exit 0, got ${status}:\n${stdout}`);
});

test('make lint runs the implemented lint legs', () => {
  const { status, stdout } = runMake('lint');
  assert.equal(status, 0, `make lint should exit 0, got ${status}:\n${stdout}`);
});

test('make build runs the implemented build legs', () => {
  const { status, stdout } = runMake('build');
  assert.equal(status, 0, `make build should exit 0, got ${status}:\n${stdout}`);
});

for (const target of ['test-postgres', 'test-mysql', 'test-db-matrix']) {
  test(`make ${target} refuses with a prerequisite notice instead of a false pass`, () => {
    const { status, stdout } = runMake(target);
    assert.notEqual(status, 0, `make ${target} must not exit 0 until its dialect matrix exists`);
    assert.match(stdout, PREREQUISITE_NOTE, `make ${target} should explain the missing prerequisite:\n${stdout}`);
  });
}
