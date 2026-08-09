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

## TDD Workflow: Isolated Test/Implementation Context

For any feature above trivial complexity, split test-writing and implementation into two separate subagent dispatches with isolated context, so the implementer cannot see the test-writer's reasoning — only the resulting test file and the feature's declared behavior.

1. Dispatch a test-writer subagent with only the feature's `behavior`, `verification`, and relevant `source_refs` from `feature_list.json` — not the implementation plan or existing implementation code. It writes the failing test(s) and reports back the test file path(s) only.
2. Run the new test yourself in the controlling session and confirm it fails for the expected reason (missing behavior, not a typo or broken setup). This RED verification is never delegated — it is the one step that proves the two agents were actually isolated and the test is real.
3. Dispatch a fresh implementer subagent with the test file path(s) and the feature's `behavior` text, but not the test-writer's notes, reasoning, or draft implementation. It writes the minimal code to pass, and must not edit the test's assertions.
4. Run the test yourself again and confirm GREEN, then run the feature's full declared verification command(s).
5. Record both commits (test-writer, implementer) in the `progress.md` checkpoint like any other implementation slice.

Skip the split for trivial features (single-assertion, config-only changes, or `*-SCOPE-001` decomposition work) — dispatch overhead is not worth it below that bar; one subagent doing full TDD in-session is fine there. Use judgment: the split earns its cost when a feature's behavior is non-obvious enough that a single agent seeing both test and implementation risks writing the implementation to fit its own test rather than the declared behavior.

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
