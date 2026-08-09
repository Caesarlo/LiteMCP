#!/usr/bin/env node
// Structural + pass-gate validator for feature_list.json.
// Run: node scripts/validate-feature-list.js
// Exits non-zero on any violation so it can be wired into pre-commit/CI.

const fs = require("fs");
const path = require("path");

const FILE = path.join(__dirname, "..", "feature_list.json");
const REQUIRED_FIELDS = [
  "id",
  "priority",
  "milestone",
  "area",
  "title",
  "behavior",
  "status",
  "dependencies",
  "verification",
  "evidence",
  "source_refs",
  "implementation",
  "blocker",
  "notes",
];
const ALLOWED_STATUSES = ["not_started", "in_progress", "blocked", "passing"];

const errors = [];
const warnings = [];

function fail(msg) {
  errors.push(msg);
}

function warn(msg) {
  warnings.push(msg);
}

let raw;
try {
  raw = fs.readFileSync(FILE, "utf8");
} catch (e) {
  console.error(`Cannot read ${FILE}: ${e.message}`);
  process.exit(1);
}

let data;
try {
  data = JSON.parse(raw);
} catch (e) {
  console.error(`feature_list.json is not valid JSON: ${e.message}`);
  process.exit(1);
}

if (!Array.isArray(data.features) || data.features.length === 0) {
  fail("`features` must be a non-empty array.");
}
if (!Array.isArray(data.milestones) || data.milestones.length === 0) {
  fail("`milestones` must be a non-empty array.");
}

const milestoneIds = new Set((data.milestones || []).map((m) => m.id));
const features = Array.isArray(data.features) ? data.features : [];

// Unique IDs
const seenIds = new Map();
for (const f of features) {
  if (!f || typeof f.id !== "string") continue;
  if (seenIds.has(f.id)) {
    fail(`Duplicate feature id: ${f.id}`);
  }
  seenIds.set(f.id, f);
}

// Required fields, allowed statuses, milestone references
for (const f of features) {
  const label = f && f.id ? f.id : "<missing id>";
  for (const field of REQUIRED_FIELDS) {
    if (!(field in (f || {}))) {
      fail(`${label}: missing required field "${field}"`);
    }
  }
  if (f && f.status && !ALLOWED_STATUSES.includes(f.status)) {
    fail(`${label}: invalid status "${f.status}" (allowed: ${ALLOWED_STATUSES.join(", ")})`);
  }
  if (f && f.milestone && !milestoneIds.has(f.milestone)) {
    fail(`${label}: references unknown milestone "${f.milestone}"`);
  }
  if (f && !Array.isArray(f.verification)) {
    fail(`${label}: "verification" must be an array`);
  } else if (f && f.status === "passing" && f.verification.length === 0) {
    fail(`${label}: status is "passing" but has no verification entries`);
  }
}

// Dependency references
const idSet = new Set(features.map((f) => f && f.id).filter(Boolean));
for (const f of features) {
  if (!f || !Array.isArray(f.dependencies)) continue;
  for (const dep of f.dependencies) {
    if (!idSet.has(dep)) {
      fail(`${f.id}: dependency "${dep}" does not exist`);
    }
  }
}

// Dependency cycle detection (DFS)
const WHITE = 0;
const GRAY = 1;
const BLACK = 2;
const color = new Map(features.map((f) => [f.id, WHITE]));
const depMap = new Map(features.map((f) => [f.id, Array.isArray(f.dependencies) ? f.dependencies : []]));

function visit(id, stack) {
  color.set(id, GRAY);
  stack.push(id);
  for (const dep of depMap.get(id) || []) {
    if (!idSet.has(dep)) continue; // already reported above
    if (color.get(dep) === GRAY) {
      const cycleStart = stack.indexOf(dep);
      fail(`Dependency cycle: ${stack.slice(cycleStart).concat(dep).join(" -> ")}`);
    } else if (color.get(dep) === WHITE) {
      visit(dep, stack);
    }
  }
  stack.pop();
  color.set(id, BLACK);
}
for (const f of features) {
  if (color.get(f.id) === WHITE) visit(f.id, []);
}

// Pass-gate: passing requires non-empty evidence
for (const f of features) {
  if (!f) continue;
  if (f.status === "passing" && (!Array.isArray(f.evidence) || f.evidence.length === 0)) {
    fail(`${f.id}: status is "passing" but "evidence" is empty`);
  }
}

// Single-active-feature rule
const inProgress = features.filter((f) => f && f.status === "in_progress");
if (inProgress.length > 1) {
  fail(
    `Single-active-feature rule violated: ${inProgress.length} features are in_progress (${inProgress
      .map((f) => f.id)
      .join(", ")})`
  );
}

// A feature should not depend on a feature that is not passing while itself being passing
for (const f of features) {
  if (!f || f.status !== "passing") continue;
  for (const dep of f.dependencies || []) {
    const depFeature = seenIds.get(dep);
    if (depFeature && depFeature.status !== "passing") {
      fail(`${f.id}: is "passing" but depends on "${dep}" which is "${depFeature.status}"`);
    }
  }
}

// Report
const passing = features.filter((f) => f && f.status === "passing").length;
const notStarted = features.filter((f) => f && f.status === "not_started").length;
const blocked = features.filter((f) => f && f.status === "blocked").length;

console.log(
  `Checked ${features.length} features across ${(data.milestones || []).length} milestones: ` +
    `${passing} passing, ${inProgress.length} in_progress, ${blocked} blocked, ${notStarted} not_started.`
);

if (warnings.length) {
  console.log(`\nWarnings (${warnings.length}):`);
  for (const w of warnings) console.log(`  - ${w}`);
}

if (errors.length) {
  console.error(`\nErrors (${errors.length}):`);
  for (const e of errors) console.error(`  - ${e}`);
  process.exit(1);
}

console.log("feature_list.json is structurally valid.");
