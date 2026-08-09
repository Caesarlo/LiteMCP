// Automated gate for M0-BOOT-001 (root Compose orchestration).
// Run: node --test scripts/validate-compose.test.js
// Dependency-free node:test gate, mirroring scripts/validate-make-help.test.js.
//
// Declared feature verification: `docker compose config` run at the repository
// root parses successfully (exit 0) with the service set complete
// ("Compose 配置解析成功且服务齐全").
//
// Documented structure encoded from docs/architecture/00-overview.md (§5.3, §7)
// and docs/architecture/08-implementation-plan.md (§M0):
//   - five services: database, redis, backend, worker, frontend
//   - worker is its own service that shares backend's application image but uses
//     a different start command/entrypoint
//   - the default database profile is PostgreSQL
//
// `docker compose config` is a pure configuration parse and does NOT require a
// running Docker daemon, so this gate is deterministic, offline and non-flaky.

"use strict";

const { execFileSync } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const assert = require("node:assert/strict");

const REPO_ROOT = path.resolve(__dirname, "..");
const REQUIRED_SERVICES = ["database", "redis", "backend", "worker", "frontend"];
const COMPOSE_FILES = [
  "docker-compose.yml",
  "docker-compose.yaml",
  "compose.yml",
  "compose.yaml",
];

// True when a Compose file is present at the repository root; used only to give
// a controller a precise diagnosis when `docker compose config` fails.
function composeFileExists() {
  return COMPOSE_FILES.some((name) => fs.existsSync(path.join(REPO_ROOT, name)));
}

function compose(args) {
  try {
    const stdout = execFileSync("docker", ["compose", ...args], {
      cwd: REPO_ROOT,
      encoding: "utf8",
    });
    return { status: 0, stdout, stderr: "" };
  } catch (err) {
    return {
      status: err.status ?? 1,
      stdout: String(err.stdout ?? ""),
      stderr: String(err.stderr ?? ""),
      cause: err.code ? ` [${err.code}]` : "",
    };
  }
}

function missingComposeHint() {
  return composeFileExists()
    ? "a Compose file exists at the repository root but `docker compose config` did not parse it"
    : "no Compose file found at the repository root (expected docker-compose.yml, see docs/architecture/00-overview.md §7)";
}

test("docker compose config parses at the repository root", () => {
  const r = compose(["config"]);
  assert.equal(
    r.status,
    0,
    `docker compose config must exit 0 at ${REPO_ROOT}; got status=${r.status}${r.cause}. ` +
      `${missingComposeHint()}\nstdout:\n${r.stdout}\nstderr:\n${r.stderr}`
  );
});

test("compose service set includes database, redis, backend, worker and frontend", () => {
  const r = compose(["config", "--services"]);
  assert.equal(
    r.status,
    0,
    `docker compose config --services must exit 0 at ${REPO_ROOT}; got status=${r.status}${r.cause}. ` +
      `${missingComposeHint()}\nstdout:\n${r.stdout}\nstderr:\n${r.stderr}`
  );
  const actual = new Set(
    r.stdout
      .split(/\r?\n/)
      .map((line) => line.trim())
      .filter(Boolean)
  );
  const missing = REQUIRED_SERVICES.filter((name) => !actual.has(name));
  assert.deepEqual(
    missing,
    [],
    `Compose file is present but missing required service(s): ${missing.join(", ")}. ` +
      `Present services: ${[...actual].sort().join(", ")}`
  );
});

test("backend and worker share one application image with different startup commands", () => {
  const r = compose(["config", "--format", "json"]);
  assert.equal(
    r.status,
    0,
    `docker compose config --format json must exit 0 at ${REPO_ROOT}; got status=${r.status}${r.cause}. ` +
      `${missingComposeHint()}\nstdout:\n${r.stdout}\nstderr:\n${r.stderr}`
  );
  let services;
  try {
    services = JSON.parse(r.stdout).services ?? {};
  } catch (err) {
    assert.fail(`docker compose config --format json did not return valid JSON: ${err.message}`);
  }
  for (const name of ["backend", "worker"]) {
    assert.ok(services[name], `compose config is missing service '${name}'`);
  }
  const backend = services.backend;
  const worker = services.worker;

  // The application source may be declared as a shared image or a shared build
  // context; both satisfy the "与 backend 同镜像" intent in 00-overview.md §5.3.
  const source = (svc) => {
    if (typeof svc.image === "string" && svc.image.length > 0) {
      return { kind: "image", value: svc.image };
    }
    if (svc.build && typeof svc.build.context === "string" && svc.build.context.length > 0) {
      return { kind: "build", value: svc.build.context };
    }
    return null;
  };
  const backendSource = source(backend);
  const workerSource = source(worker);
  assert.ok(
    backendSource &&
      workerSource &&
      backendSource.kind === workerSource.kind &&
      backendSource.value === workerSource.value,
    `backend and worker must share the same application image (00-overview.md §5.3, 08-implementation-plan.md §M0). ` +
      `backend source=${JSON.stringify(backendSource)}, worker source=${JSON.stringify(workerSource)}`
  );

  const canon = (value) => JSON.stringify(value ?? null);
  const sameCommand = canon(backend.command) === canon(worker.command);
  const sameEntrypoint = canon(backend.entrypoint) === canon(worker.entrypoint);
  assert.ok(
    !sameCommand || !sameEntrypoint,
    `backend and worker must use different start command/entrypoint (00-overview.md §5.3, 08-implementation-plan.md §M0). ` +
      `backend command=${canon(backend.command)} entrypoint=${canon(backend.entrypoint)}; ` +
      `worker command=${canon(worker.command)} entrypoint=${canon(worker.entrypoint)}`
  );
});

test("the default database service runs PostgreSQL", () => {
  const r = compose(["config", "--format", "json"]);
  assert.equal(
    r.status,
    0,
    `docker compose config --format json must exit 0 at ${REPO_ROOT}; got status=${r.status}${r.cause}. ` +
      `${missingComposeHint()}\nstdout:\n${r.stdout}\nstderr:\n${r.stderr}`
  );
  let services;
  try {
    services = JSON.parse(r.stdout).services ?? {};
  } catch (err) {
    assert.fail(`docker compose config --format json did not return valid JSON: ${err.message}`);
  }
  assert.ok(services.database, `compose config is missing service 'database'`);
  const image = typeof services.database.image === "string" ? services.database.image : "";
  assert.match(
    image,
    /postgres/i,
    `the default database service must use a PostgreSQL image (00-overview.md §5.1/§5.3), got ${JSON.stringify(image)}`
  );
});
