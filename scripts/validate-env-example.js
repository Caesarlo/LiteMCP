#!/usr/bin/env node
// Validator for the root .env.example file.
// Run: node scripts/validate-env-example.js   (or `make validate-env-example`)
// Exits non-zero on any violation so it can be wired into pre-commit/CI.
//
// Checks two properties (feature M0-ENV-002):
//   1. Coverage — every config group a developer needs to learn from the
//      example is present: database, Redis, encryption keys, storage,
//      gateway. The first three are the config values backend
//      `core/config.py` treats as required (fail fast when missing).
//   2. No real secrets — values must be placeholders. A value shaped like a
//      real credential (Fernet key, cloud/API token, PEM private key) is
//      rejected so nobody accidentally commits a live secret as an example.

"use strict";

const fs = require("fs");
const path = require("path");

// Every config group the example must document (database, Redis, encryption
// keys, storage, gateway). The first three are the values backend
// `core/config.py` treats as required (fail fast when missing).
const REQUIRED_KEYS = [
  "LITEMCP_DATABASE_URL",
  "LITEMCP_REDIS_URL",
  "LITEMCP_ENCRYPTION_KEYS",
  "LITEMCP_STORAGE_BACKEND",
  "LITEMCP_STORAGE_PATH",
  "LITEMCP_GATEWAY_ENABLED",
];

// High-precision patterns for values that are real credentials, not
// placeholders. Placeholder words like "change-me-..." or
// "litemcp_dev_password" are intentionally NOT matched.
const SECRET_PATTERNS = [
  // Fernet key: 32 random bytes urlsafe base64 (43 chars + "=" or 44 chars).
  [/^[A-Za-z0-9_-]{43}=$|^[A-Za-z0-9_-]{44}$/, "a Fernet key"],
  [/AKIA[0-9A-Z]{16}/, "an AWS access key"],
  [/ghp_[A-Za-z0-9]{36}/, "a GitHub personal access token"],
  [/xox[baprs]-[A-Za-z0-9-]{10,}/, "a Slack token"],
  [/\b(sk|rk)_(live|test)_[0-9a-zA-Z]{24,}\b/, "a Stripe API key"],
  [/\bsk-[A-Za-z0-9]{20,}\b/, "an OpenAI-style secret key"],
  [/AIza[0-9A-Za-z_-]{35}/, "a Google API key"],
  [/-----BEGIN [A-Z ]*PRIVATE KEY-----/, "a PEM private key"],
];

function parseEnv(text) {
  const entries = new Map();
  for (const rawLine of text.split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line || line.startsWith("#")) continue;
    const eq = line.indexOf("=");
    if (eq === -1) continue; // not a KEY=VALUE line
    const key = line.slice(0, eq).trim();
    const value = line.slice(eq + 1).trim();
    if (key) entries.set(key, value);
  }
  return entries;
}

function validateEnvExample(text) {
  const errors = [];
  const entries = parseEnv(text);

  for (const key of REQUIRED_KEYS) {
    if (!entries.has(key)) {
      errors.push(`missing required config "${key}"`);
      continue;
    }
    if (entries.get(key) === "") {
      errors.push(`config "${key}" must not be blank`);
    }
  }

  for (const [key, value] of entries) {
    for (const [re, label] of SECRET_PATTERNS) {
      if (re.test(value)) {
        errors.push(`value for "${key}" looks like ${label}; use a placeholder, not a real secret`);
        break;
      }
    }
  }

  return { errors };
}

// CLI: validate the committed root .env.example.
if (require.main === module) {
  const FILE = path.join(__dirname, "..", ".env.example");
  let raw;
  try {
    raw = fs.readFileSync(FILE, "utf8");
  } catch (e) {
    console.error(`Cannot read ${FILE}: ${e.message}`);
    process.exit(1);
  }
  const { errors } = validateEnvExample(raw);
  if (errors.length) {
    console.error(`Errors (${errors.length}) in ${FILE}:`);
    for (const e of errors) console.error(`  - ${e}`);
    process.exit(1);
  }
  console.log(`.env.example covers all required config groups and contains no real secrets.`);
}

module.exports = { validateEnvExample, REQUIRED_KEYS };
