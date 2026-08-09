#!/usr/bin/env node
// Validator for docs/adr/ (feature M0-ADR-001).
// Run: node scripts/validate-adr.js
// Exits non-zero on any violation so it can be wired into pre-commit/CI.
//
// Checks two properties:
//   1. Structure — every numbered ADR file (NNNN-slug.md) has a Status line
//      and the three required Nygard-style sections (Context, Decision,
//      Consequences).
//   2. Coverage — the six M0-mandated ADR topics from
//      08-implementation-plan.md L94 (MCP SDK/version, DB dialect strategy,
//      outbox/worker mechanism, feature flag registry, object
//      storage/Registry interface, production reverse proxy & trusted
//      proxy) are each covered by at least one ADR.

"use strict";

const fs = require("fs");
const path = require("path");

const ADR_FILENAME_RE = /^\d{4}-[a-z0-9-]+\.md$/;
const IGNORED_FILENAMES = new Set(["README.md", "template.md"]);
const MIN_ADR_COUNT = 6;

const REQUIRED_SECTIONS = ["## Context", "## Decision", "## Consequences"];

// One pattern per M0-mandated topic (08-implementation-plan.md L94). Each
// topic is satisfied if ANY numbered ADR's content matches its pattern.
const REQUIRED_TOPICS = [
  { name: "MCP SDK/version", pattern: /MCP SDK|MCP.{0,10}(2025-11-25|协议版本)/ },
  { name: "DB dialect strategy", pattern: /PostgreSQL[\s\S]{0,40}MySQL|方言/ },
  { name: "outbox/worker mechanism", pattern: /outbox/i },
  { name: "feature flag registry", pattern: /feature flag/i },
  { name: "object storage/Registry interface", pattern: /StorageBackend|Registry/ },
  { name: "production reverse proxy & trusted proxy", pattern: /trusted proxy|反向代理/i },
];

function validateAdrSet(files) {
  const errors = [];
  const adrFiles = files.filter((f) => !IGNORED_FILENAMES.has(f.filename));

  for (const file of adrFiles) {
    if (!ADR_FILENAME_RE.test(file.filename)) {
      errors.push(`"${file.filename}" does not match the required NNNN-slug.md naming pattern`);
      continue;
    }
    if (!/^- Status:/m.test(file.content)) {
      errors.push(`"${file.filename}" is missing a "- Status:" line`);
    }
    for (const section of REQUIRED_SECTIONS) {
      if (!file.content.includes(section)) {
        errors.push(`"${file.filename}" is missing required section "${section}"`);
      }
    }
  }

  const numberedAdrs = adrFiles.filter((f) => ADR_FILENAME_RE.test(f.filename));
  if (numberedAdrs.length < MIN_ADR_COUNT) {
    errors.push(`found ${numberedAdrs.length} numbered ADR(s); at least ${MIN_ADR_COUNT} are required (M0 exit criteria, 08-implementation-plan.md L94)`);
  }

  const combined = numberedAdrs.map((f) => f.content).join("\n");
  for (const topic of REQUIRED_TOPICS) {
    if (!topic.pattern.test(combined)) {
      errors.push(`no ADR covers required M0 topic "${topic.name}"`);
    }
  }

  return { errors };
}

// CLI: validate the committed docs/adr directory.
if (require.main === module) {
  const DIR = path.join(__dirname, "..", "docs", "adr");
  let entries;
  try {
    entries = fs.readdirSync(DIR);
  } catch (e) {
    console.error(`Cannot read ${DIR}: ${e.message}`);
    process.exit(1);
  }
  const files = entries
    .filter((name) => name.endsWith(".md"))
    .map((name) => ({ filename: name, content: fs.readFileSync(path.join(DIR, name), "utf8") }));

  const { errors } = validateAdrSet(files);
  if (errors.length) {
    console.error(`Errors (${errors.length}) in ${DIR}:`);
    for (const e of errors) console.error(`  - ${e}`);
    process.exit(1);
  }
  console.log(`docs/adr/ has ${files.length - 2} numbered ADR(s) covering all ${REQUIRED_TOPICS.length} required M0 topics.`);
}

module.exports = { validateAdrSet, REQUIRED_TOPICS, REQUIRED_SECTIONS, ADR_FILENAME_RE };
