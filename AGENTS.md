# LiteMCP Agent Working Agreement

This repository is designed for long-running, multi-session implementation work. Durable repository artifacts, not chat history, define scope and verified progress.

## Source Of Truth

- `feature_list.json` is the single source of truth for feature scope, priority, dependency, verification, evidence, and state.
- `progress.md` records the current verified snapshot, append-only session checkpoints, decisions, risks, and the next best step.
- Architecture and UI documents define intended behavior, but do not prove that behavior is implemented.
- Existing code does not count as passing until the feature's declared verification succeeds and evidence is recorded.

## Startup Workflow

Before writing implementation code:

1. Confirm the repository root.
2. Run `node scripts/validate-feature-list.js`. It must exit 0 before any other step; if it fails, fixing `feature_list.json` is the narrow task for this session.
3. If `git config core.hooksPath` is not yet set to `.githooks` in this clone, run `git config core.hooksPath .githooks` once so `feature_list.json` edits are checked on every commit.
4. Read `progress.md`, especially `Current Verified State` and the latest session.
5. Read `feature_list.json` and locate any `in_progress` or `blocked` feature.
6. Review the relevant architecture documents listed in the feature's `source_refs`.
7. Inspect recent Git status and commits without discarding user changes.
8. Run the active feature's baseline or focused verification when it already exists.
9. Continue the existing `in_progress` feature; otherwise select the highest-priority `not_started` feature whose dependencies are all `passing`.
10. If the selected feature is a `*-SCOPE-001` placeholder (M2 and later milestones), its only allowed action is decomposition: replace it with session-sized features per its own `behavior` text and `verification` entry, never mark the placeholder itself `passing`.

If the baseline is already broken, record that fact and repair the narrow baseline issue before stacking new feature work on it.

## Feature Execution Rules

- Only one feature may have status `in_progress` at a time.
- A conversation may complete several features sequentially, but each feature must pass its gate before the next becomes active.
- Keep work inside the active feature's observable behavior and acceptance boundary.
- Narrow supporting changes are allowed only when recorded in `progress.md`.
- Do not silently weaken, replace, or skip a feature's verification.
- If new required work is discovered, add a new feature with behavior, dependency, verification, and priority instead of leaving an untracked TODO.
- Do not mark planned architecture as implemented merely because it appears in documentation.

## Passing Requires Evidence

A feature may move to `passing` only when all of the following are true:

- The complete behavior declared by the feature is implemented.
- Every required verification command has actually run successfully.
- The observed result is recorded in the feature's `evidence` array.
- No blocking defect remains in the verified path.
- Existing relevant checks remain green.
- A matching checkpoint has been appended to `progress.md`.

Code written without completed verification remains `in_progress`. A failing or unavailable dependency must be recorded explicitly; use `blocked` only when work genuinely cannot proceed.

Passing is monotonic. If a regression is later discovered, create a regression feature that depends on the original feature instead of erasing historical evidence.

## Progress Checkpoints

Append a checkpoint to `progress.md` immediately after any of these events:

- A feature enters `in_progress`, `blocked`, or `passing`.
- A significant implementation slice is completed.
- A verification command succeeds or fails.
- A public contract, schema, migration, security boundary, or publication invariant changes.
- A material architecture decision is made.
- Existing code and documented intent are found to disagree.
- A blocker, deferred path, or newly discovered feature is identified.

Each checkpoint records the feature ID, result, status change when applicable, files changed, verification performed, evidence, risks, and the next action.

## CodeGraph Workflow

Use the configured CodeGraph tools for structural questions:

- Start architecture or feature context work with `codegraph_context`.
- Use `codegraph_trace` for end-to-end symbol flows.
- Use `codegraph_search`, `codegraph_callers`, `codegraph_callees`, and `codegraph_impact` for symbol and change-impact questions.
- Use `codegraph_files` for indexed project structure and `codegraph_explore` for related source bodies.
- Use native search only for literal text, non-indexed files, or specific files identified as stale by CodeGraph.

## End Of Session

Before ending an implementation session:

1. Run the active feature's required verification and relevant regression checks.
2. Update `feature_list.json` state, implementation paths, and evidence.
3. Append the final session checkpoint to `progress.md`.
4. Refresh `Current Verified State`, blocker, and next best step.
5. Confirm no more than one feature is `in_progress`.
6. Record every unfinished or unverified path; do not leave ambiguous partial work.
7. Remove temporary debug artifacts created during the session.
8. Leave the standard startup and verification path usable, or record the exact reason it is not.

## Clean-State Checklist

- Required build or focused checks pass.
- Relevant existing tests still pass.
- Feature state matches verified reality.
- Verification evidence is durable and reproducible.
- Progress and risks are recorded.
- No undocumented half-finished step remains.
- The next session can continue without relying on chat history.
