# Read-only Executive Brain Cycle v1

## Purpose

Provide one repeatable deterministic cycle that combines portfolio triage with measured execution-outcome feedback. This is the first executable slice of the executive brain; it is not an always-on model or an autonomous dispatcher.

## Run

```bash
python -m engine.cli brain-cycle
python -m engine.cli brain-cycle --limit 5
```

The command reads up to 200 task records through the existing task-store adapter. When `RAAWAA_ENGINE_API_URL` is configured, this uses the existing authenticated remote D1 task adapter. It does not create a second queue or scheduler.

## What the cycle computes

1. Reuses `engine.executive_intelligence` for status counts, expired-lease visibility, explainable pending-task triage, and next checks.
2. Groups terminal `succeeded` and `failed` outcomes by task type.
3. Marks samples smaller than five as insufficient evidence.
4. Flags task types with at least five terminal samples and a failure share of 20% or more for investigation.
5. Explicitly separates execution reliability from semantic quality. A succeeded task is not proof that its output passed independent review.
6. Emits decision candidates with their evidence source; candidates are not dispatched.

## Learning and anti-self-reinforcement rules

- No model-generated score is used to rank or authorize tasks.
- No automatic prompt, policy, budget, permission, provider, or tool changes.
- No memory writes. The current memory store is local SQLite and is not yet a verified shared cloud memory for remote scheduled runs.
- No scorecard is considered reliable below five terminal outcomes.
- Scorecards describe execution reliability only. A future quality score requires persisted independent reviewer evidence and acceptance-check results.
- Every observation is traceable to the task ledger snapshot and can be recomputed.
- The first release intentionally has no automatic promotion or tuning; future changes must be proposed, evaluated against a frozen regression set and a separate holdout set, reviewed, versioned, and rollback-capable.

## Side-effect contract

This command performs zero task writes, claims, queue reordering, provider/model calls, external writes, budget changes, permission changes, or production operations. It does not start a workflow or alter the scheduler.

## Known limitations and next increments

- The task adapter currently returns a bounded recent-task snapshot; the scorecard may not represent the entire historical population.
- There is no independent reviewer-result field in the current task outcome contract, so semantic quality cannot yet be measured.
- This cycle is invoked explicitly or by a separately approved existing workflow; it does not add a new cron.
- A later stage may persist a compact cycle record through an existing approved durable store after schema, retention, idempotency, and privacy review.
- A later stage may add model-assisted planning only after a provider's free quota, commercial terms, data handling, account eligibility, and hard stop are verified. If no eligible route exists, deterministic monitoring remains functional.
