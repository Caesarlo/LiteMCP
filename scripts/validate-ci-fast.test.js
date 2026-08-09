// Automated gate for M0-CI-001 (CI fast gate: backend lint/type/unit + frontend lint/type/unit/build).
// Dependency-free, mirrors the node:test style of scripts/validate-make-help.test.js.
// This gate checks that `make ci-fast` exists, exits 0, and honestly wires in all
// seven legs rather than echoing success.
const { execFileSync } = require('node:child_process');
const test = require('node:test');
const assert = require('node:assert/strict');

// The seven legs `make ci-fast` must visibly run, identified by exact markers in its stdout.
const REQUIRED_LEG_MARKERS = [
  'backend lint',
  'backend type',
  'backend unit',
  'frontend lint',
  'frontend type',
  'frontend unit',
  'frontend build',
];

function runMake(target) {
  try {
    const stdout = execFileSync('make', [target], { encoding: 'utf8' });
    return { status: 0, stdout };
  } catch (err) {
    return { status: err.status ?? 1, stdout: String(err.stdout ?? '') };
  }
}

// Cache a single `make ci-fast` run so the exit-code and honesty checks share one capture.
let CI_FAST_RUN = null;
function runCiFastOnce() {
  if (CI_FAST_RUN === null) CI_FAST_RUN = runMake('ci-fast');
  return CI_FAST_RUN;
}

test('make help lists the ci-fast target', () => {
  const { status, stdout } = runMake('help');
  assert.equal(status, 0, `make help should succeed, got exit ${status}:\n${stdout}`);
  const listed = new Set();
  for (const line of stdout.split(/\r?\n/)) {
    const m = line.match(/^\s*make\s+([A-Za-z0-9_-]+)/);
    if (m) listed.add(m[1]);
  }
  assert.ok(listed.has('ci-fast'), "make help must list target 'ci-fast'");
});

test('make ci-fast exits 0 (all seven legs genuinely pass)', () => {
  const { status, stdout } = runCiFastOnce();
  assert.equal(status, 0, `make ci-fast should exit 0, got ${status}:\n${stdout}`);
});

test('make ci-fast output honestly shows all seven legs', () => {
  const { stdout } = runCiFastOnce();
  for (const marker of REQUIRED_LEG_MARKERS) {
    assert.ok(
      stdout.includes(marker),
      `make ci-fast output must contain marker '${marker}':\n${stdout}`
    );
  }
});
