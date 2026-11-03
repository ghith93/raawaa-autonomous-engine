# Claude audit — Cycle 1 (Discovery + partial code audit)

Date: 2026-11-04
Baseline: `main` @ `bdaf97006aedd431f051c0fec8a182037ed0320b`
Scope of this document: documentation only. No source, workflow, config, or policy file is changed.
Auditor: Claude (read-only GitHub access + branch/PR write). No code was executed and no CI was run by the auditor.

## 0. Limits of this audit (read first)

- Policy documents actually read in this cycle: `AGENTS.md`, `docs/architecture.md`.
- NOT read in this cycle: `docs/master-autonomous-execution-directive.ar.md`, `docs/continuous-improvement-charter.md`, `docs/operations.md` (51 KB), `docs/operations/asset-register.md`, `docs/operations/tooling-capability-inventory-2026-11-03.md`, `config/*.json`, Worker (`worker/`), migrations, tests, workflows.
- Per `AGENTS.md`, a runtime that cannot read all mandatory policy must say so. Findings below are therefore PARTIAL and must not be treated as a full audit.
- Code files read: `engine/worker.py`, `engine/agent_runtime.py`, `engine/task_store.py`, `engine/quota_live_gateway.py`.
- Tests were not executed. Every finding below is a static-reading observation (status: OBSERVED_IN_SOURCE), not a reproduced failure.

## 1. Repository state observed

- Open PRs at read time: #442 (security: hard-disable Codespaces), #441 (docs reconcile), #426, #423, #421, #405, #380 (drafts).
- PR #442: head moved during the session; check run `test` was reported `in_progress` at read time. Not verified as passing.

## 2. Findings

### F1 — A "succeeded" task can be a no-op dry run (Medium, semantics/observability)
- Evidence: `engine/worker.py` default branch sets `result = contract`; `engine/agent_runtime.py::build_task_contract` returns `mode: "dry_run"`, `execution_started: False`, `provider_calls: 0`. `run_once` then calls `store.finish(..., "succeeded", ...)`.
- Impact: task status `succeeded` is indistinguishable from real work unless a consumer inspects `result.mode`. Dashboards and executive ranking that count `succeeded` can overstate completed work. This is exactly the "successful workflow ≠ completed task" confusion the directive warns about.
- Recommendation (PROPOSED): add a distinct terminal state or a first-class column (e.g. `execution_mode`) and make status/dashboard surfaces report dry-run completions separately. Additive migration only; must be mirrored in SQLite `TaskStore`, D1 schema, and Worker.

### F2 — Pre-provider failures are terminal `blocked`, not deferred (Medium-High, reliability)
- Evidence: in `engine/worker.py`, any exception from `live_gateway.generate()` leads to `store.finish(..., "blocked", error="live_inference_blocked:<Type>")` and the task is not retried. In `engine/quota_live_gateway.py`, several `ProviderUnavailable` paths are provably pre-send (kill switch off, no route with remaining quota, monetary budget exceeded, durable step could not be prepared) while others are ambiguous post-send (non-429 provider error, adapter failure). The same exception class covers both.
- Impact: a transient capacity shortage (daily quota exhausted, cooldown after 429) permanently blocks the task instead of waiting for the next window. Ambiguous cases must stay blocked (correct, avoids double billing/replay).
- Recommendation (PROPOSED): introduce a subclass such as `ProviderNotCalled(ProviderUnavailable)` raised only on provably pre-send paths, and have the worker defer those tasks (requeue with backoff, without weakening any quota or accounting rule). Ambiguous errors keep current behavior. Needs tests for each path, including the lost-response case.

### F3 — Live output cap of 256 tokens limits usefulness (Medium, capability)
- Evidence: `LIVE_MAX_OUTPUT_TOKENS = 256` in `engine/worker.py`; one inference step per task (`step_key="primary-inference:v1"`).
- Impact: research briefs, code reviews and audits cannot produce substantive output; the engine cannot do multi-step work yet. Consistent with `docs/architecture.md`, which states the roster/planner are declarative, not a persistent multi-agent runtime.
- Recommendation (PROPOSED): per-task-type output budgets taken from policy (still bounded by existing per-task/daily caps), and multi-step execution with a distinct `step_key` per step (the invocation-checkpoint design already supports this).

### F4 — Task type lists disagree (Low)
- Evidence: `TASK_AGENT_CONTRACTS` in `engine/agent_runtime.py` includes `mobile_test`; `SAFE_TASKS` in `engine/worker.py` does not. A `mobile_test` task claimed by this worker is finished as `blocked: unsupported_task_type`.
- Impact: possibly intentional (ARTEMIS is read-only/disabled), but the two allowlists can drift. Recommendation (PROPOSED): derive `SAFE_TASKS` from one source of truth plus an explicit "enabled for this runtime" set, with a test asserting consistency.

### F5 — Queue has no priority or dependency semantics (Medium, orchestration)
- Evidence: `TaskStore.claim_next` selects `ORDER BY created_at,id LIMIT 1`; no dependency or priority columns. `docs/architecture.md` states multi-step workflow plans remain previews because dependencies are not enforced in SQLite or D1.
- Impact: the executive/orchestrator layer cannot actually execute a DAG. Recommendation (PROPOSED): additive `priority` + `depends_on` columns, claim only tasks whose dependencies are `succeeded` (and real, not dry-run per F1), implemented identically in SQLite and D1/Worker, with concurrency and migration tests.

## 3. Strengths observed (static reading)

- Fenced leases: `finish`/`retry_or_fail` assert lease ownership and expiry; stale leases are recovered with the old token cleared.
- Quota gateway design is conservative: deterministic idempotency/reservation IDs per route, 429 rotates only to another verified route, ambiguous failures are never replayed or released, non-zero cost on a "free" route quarantines the route and fails closed, no paid fallback.
- Policy is loaded and fail-closed before claiming work (`load_operating_policy`), with the untrusted payload placed after the trusted policy in the prompt.

## 4. Status labels

| Item | Status |
|---|---|
| F1–F5 findings | OBSERVED_IN_SOURCE (not reproduced, tests not run) |
| Fixes for F1–F5 | PROPOSED |
| Full repository audit | NOT_IMPLEMENTED (only partial coverage, see section 0) |
| External technology research | NOT_IMPLEMENTED in this cycle |

## 5. Next cycle (suggested order)

1. Read remaining mandatory policy docs and `config/*.json`; trace the Worker/D1 task path and migrations.
2. Implement F2 (smallest, safest, strong reliability benefit) on its own branch with tests in `tests/`, verify exact-head CI.
3. F1 status semantics, then F5 dependencies.
4. Then external research with dated primary sources, per the directive.

No spending, secret, permission, billing, or production change is requested or made by this document.
