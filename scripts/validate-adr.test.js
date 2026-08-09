// Tests for validate-adr.js.
// Run: node --test scripts/validate-adr.test.js
// Verifies the gate that keeps docs/adr/ structurally sound: every numbered
// ADR file has the four required Nygard-style sections, and the six M0-
// mandated topics (08-implementation-plan.md L94) are each covered by at
// least one ADR.

"use strict";

const { test } = require("node:test");
const assert = require("node:assert/strict");

const { validateAdrSet, REQUIRED_TOPICS } = require("./validate-adr.js");

function adr(overrides = {}) {
  const base = {
    filename: "0001-example-decision.md",
    content: [
      "# ADR-0001: Example decision",
      "",
      "- Status: Accepted",
      "- Date: 2026-08-09",
      "- Source refs: docs/architecture/00-overview.md",
      "",
      "## Context",
      "Some context.",
      "",
      "## Decision",
      "We will do X.",
      "",
      "## Consequences",
      "Some consequences.",
      "",
    ].join("\n"),
  };
  return { ...base, ...overrides };
}

function fullTopicCoverage() {
  return [
    adr({ filename: "0001-mcp-sdk.md", content: adr().content + "\nMCP SDK 2025-11-25\n" }),
    adr({ filename: "0002-db-dialect.md", content: adr().content + "\nPostgreSQL 14+ 与 MySQL 8.0+ 方言\n" }),
    adr({ filename: "0003-outbox.md", content: adr().content + "\noutbox/worker 至少一次投递\n" }),
    adr({ filename: "0004-flags.md", content: adr().content + "\nfeature flag registry\n" }),
    adr({ filename: "0005-storage.md", content: adr().content + "\nStorageBackend 与 OCI Registry\n" }),
    adr({ filename: "0006-proxy.md", content: adr().content + "\n只信任显式配置的反向代理，trusted proxy\n" }),
  ];
}

test("a complete set of 6 ADRs covering all required topics passes", () => {
  const { errors } = validateAdrSet(fullTopicCoverage());
  assert.deepEqual(errors, []);
});

test("fewer than 6 numbered ADR files is reported", () => {
  const { errors } = validateAdrSet(fullTopicCoverage().slice(0, 3));
  assert.ok(errors.some((e) => /at least 6|六篇|6 篇/.test(e)));
});

test("a missing Decision section is reported by filename", () => {
  const files = fullTopicCoverage();
  files[0] = adr({
    filename: files[0].filename,
    content: files[0].content.replace("## Decision\nWe will do X.\n\n", ""),
  });
  const { errors } = validateAdrSet(files);
  assert.ok(errors.some((e) => e.includes(files[0].filename) && /Decision/.test(e)));
});

test("a missing Status line is reported by filename", () => {
  const files = fullTopicCoverage();
  files[0] = adr({
    filename: files[0].filename,
    content: files[0].content.replace("- Status: Accepted\n", ""),
  });
  const { errors } = validateAdrSet(files);
  assert.ok(errors.some((e) => e.includes(files[0].filename) && /Status/.test(e)));
});

test("a filename not matching NNNN-slug.md is reported", () => {
  const files = fullTopicCoverage();
  files[0] = { ...files[0], filename: "mcp-sdk-decision.md" };
  const { errors } = validateAdrSet(files);
  assert.ok(errors.some((e) => e.includes("mcp-sdk-decision.md")));
});

test("README.md and template.md are ignored, not treated as ADRs", () => {
  const files = [
    ...fullTopicCoverage(),
    { filename: "README.md", content: "not an adr" },
    { filename: "template.md", content: "not an adr" },
  ];
  const { errors } = validateAdrSet(files);
  assert.deepEqual(errors, []);
});

test("a missing required topic (e.g. reverse proxy) is reported", () => {
  const files = fullTopicCoverage().filter((f) => f.filename !== "0006-proxy.md");
  files.push(adr({ filename: "0006-something-else.md" }));
  const { errors } = validateAdrSet(files);
  assert.ok(errors.some((e) => /reverse proxy|trusted proxy|反向代理/i.test(e)));
});

test("REQUIRED_TOPICS exposes exactly the six M0-mandated topics", () => {
  assert.equal(REQUIRED_TOPICS.length, 6);
});
