// Tests for validate-env-example.js.
// Run: node --test scripts/validate-env-example.test.js
// Verifies the gate that keeps .env.example covering every required config
// group (database, Redis, encryption keys, storage, gateway) and free of
// real secrets.

"use strict";

const { test } = require("node:test");
const assert = require("node:assert/strict");

const { validateEnvExample } = require("./validate-env-example.js");

// A complete example covering all five required groups with obvious
// placeholders and no secret-shaped values.
function completeExample(overrides = {}) {
  const lines = {
    LITEMCP_APP_NAME: "litemcp",
    LITEMCP_ENVIRONMENT: "dev",
    LITEMCP_DEBUG: "false",
    LITEMCP_LOG_LEVEL: "INFO",
    LITEMCP_DATABASE_URL: "postgresql+asyncpg://litemcp:litemcp_dev_password@localhost:5432/litemcp",
    LITEMCP_REDIS_URL: "redis://localhost:6379/0",
    LITEMCP_ENCRYPTION_KEYS: "change-me-generate-a-fernets-key",
    LITEMCP_GATEWAY_ENABLED: "false",
    LITEMCP_ALLOWED_ORIGINS: "http://localhost:5173",
    LITEMCP_TRUSTED_PROXIES: "",
    LITEMCP_STORAGE_BACKEND: "local",
    LITEMCP_STORAGE_PATH: "./data/storage",
    ...overrides,
  };
  const body = Object.entries(lines)
    .filter(([, v]) => v !== undefined) // omit a key entirely
    .map(([k, v]) => (v === "" ? `${k}=` : `${k}=${v}`)) // blank → empty value
    .join("\n");
  return `# LiteMCP backend configuration example\n${body}\n`;
}

test("complete example with all five config groups passes", () => {
  const { errors } = validateEnvExample(completeExample());
  assert.deepEqual(errors, []);
});

test("comment lines and blank lines are ignored", () => {
  const text = [
    "# just a comment",
    "",
    "LITEMCP_DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/litemcp",
    "   ",
    "# another comment",
    "LITEMCP_REDIS_URL=redis://localhost:6379/0",
    "",
    "LITEMCP_ENCRYPTION_KEYS=placeholder-not-a-real-key",
    "LITEMCP_STORAGE_BACKEND=local",
    "LITEMCP_STORAGE_PATH=./data/storage",
    "LITEMCP_GATEWAY_ENABLED=false",
  ].join("\n");
  const { errors } = validateEnvExample(text);
  assert.deepEqual(errors, []);
});

test("missing LITEMCP_DATABASE_URL is reported", () => {
  const { errors } = validateEnvExample(completeExample({ LITEMCP_DATABASE_URL: undefined }));
  assert.ok(errors.some((e) => e.includes("LITEMCP_DATABASE_URL")));
});

test("missing LITEMCP_REDIS_URL is reported", () => {
  const { errors } = validateEnvExample(completeExample({ LITEMCP_REDIS_URL: undefined }));
  assert.ok(errors.some((e) => e.includes("LITEMCP_REDIS_URL")));
});

test("missing LITEMCP_ENCRYPTION_KEYS is reported", () => {
  const { errors } = validateEnvExample(completeExample({ LITEMCP_ENCRYPTION_KEYS: undefined }));
  assert.ok(errors.some((e) => e.includes("LITEMCP_ENCRYPTION_KEYS")));
});

test("missing storage group is reported", () => {
  const { errors } = validateEnvExample(completeExample({ LITEMCP_STORAGE_BACKEND: undefined, LITEMCP_STORAGE_PATH: undefined }));
  assert.ok(errors.some((e) => e.includes("LITEMCP_STORAGE_BACKEND")));
});

test("missing gateway group is reported", () => {
  const { errors } = validateEnvExample(completeExample({ LITEMCP_GATEWAY_ENABLED: undefined }));
  assert.ok(errors.some((e) => e.includes("LITEMCP_GATEWAY_ENABLED")));
});

test("blank value for a required key is reported", () => {
  const { errors } = validateEnvExample(completeExample({ LITEMCP_DATABASE_URL: "" }));
  assert.ok(errors.some((e) => e.includes("LITEMCP_DATABASE_URL")));
});

test("a real Fernet key value is rejected as a secret", () => {
  const fernet = "uQnT2J9KZ7bF1x8pVwY4rE6aS0dH3gLm5iCk7jXq2zF=";
  const { errors } = validateEnvExample(completeExample({ LITEMCP_ENCRYPTION_KEYS: fernet }));
  assert.ok(errors.some((e) => /encryption key|secret/i.test(e)));
});

test("an AWS access key is rejected as a secret", () => {
  const { errors } = validateEnvExample(
    completeExample({ LITEMCP_ENCRYPTION_KEYS: "AKIAIOSFODNN7EXAMPLE" })
  );
  assert.ok(errors.some((e) => /secret/i.test(e)));
});

test("a GitHub personal access token is rejected as a secret", () => {
  const { errors } = validateEnvExample(
    completeExample({ LITEMCP_ENCRYPTION_KEYS: "ghp_" + "A".repeat(36) })
  );
  assert.ok(errors.some((e) => /secret/i.test(e)));
});

test("a PEM private key block is rejected as a secret", () => {
  const pem = "-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBgkqhkiG9w0B\n-----END PRIVATE KEY-----";
  const { errors } = validateEnvExample(completeExample({ LITEMCP_DATABASE_URL: pem }));
  assert.ok(errors.some((e) => /secret/i.test(e)));
});

test("non-secret placeholder values are not flagged", () => {
  const { errors } = validateEnvExample(
    completeExample({ LITEMCP_ENCRYPTION_KEYS: "change-me-generate-a-fernets-key" })
  );
  assert.deepEqual(errors, []);
});
