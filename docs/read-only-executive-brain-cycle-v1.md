# Read-only Executive Brain Cycle v1

## Purpose

Provide one repeatable deterministic cycle that combines portfolio triage with measured execution-outcome feedback. This is the first executable slice of the executive brain; it is not an always-on model or an autonomous dispatcher.

## Run

```bash
python -m engine.cli brain-cycle
python -m engine.cli brain-cycle --limit 5
```

The command asks the task-store adapter for up to **100** records, newest first, and reads whatever it returns.

The limit is 100 rather than 200 on purpose. `RemoteTaskStore.list_tasks` clamps its argument to 100 (`engine/remote_task_store.py`), and `RemoteTaskStore._request` rejects any response larger than 64 KB — task rows carry `payload_json`, so a larger request would fail rather than truncate. Requesting 200 therefore returns 100 in production while returning 200 locally, which is two different denominators chosen by an environment variable. Both adapters honour 100.

When `RAAWAA_ENGINE_API_URL` is configured, the existing authenticated remote D1 adapter is used. No second queue or scheduler is created.

`--limit` (1–50, default 10) is a **portfolio view control only**: it bounds the triage recommendation list. Reliability scorecards always consume the full snapshot, because trimming them would silently change the measured denominator.

## What the cycle computes

1. Reuses `engine.executive_intelligence` for status counts, expired-lease visibility, explainable pending-task triage, and next checks.
2. Groups terminal `succeeded` and `failed` outcomes by task type, on the **stripped** task type, so whitespace variants do not split a sample in half.
3. Marks samples smaller than five as insufficient evidence.
4. Reports every failure share with a **Wilson 95% interval** and escalates in two steps:
   - `reliability_needs_review` — the interval's **lower bound** is at or above 20%;
   - `reliability_watch` — the point estimate is at or above 20% but the interval still reaches below it.
5. Explicitly separates execution reliability from semantic quality. A succeeded task is not proof that its output passed independent review.
6. Emits decision candidates with their evidence source; candidates are not dispatched.

Why the interval matters, computed rather than quoted:

| failures / n | point estimate | Wilson 95% |
|---|---|---|
| 1 / 5 | 20.0% | **3.6% – 62.4%** |
| 2 / 8 | 25.0% | 7.1% – 59.1% |
| 5 / 25 | 20.0% | 8.9% – 39.1% |
| 20 / 60 | 33.3% | 22.7% – 45.9% |
| 20 / 100 | 20.0% | 13.3% – 28.9% |

A rule that escalated on the point estimate alone would raise a high-priority review from a single failure in five, whose true failure share could plausibly be 3.6%.

## Denominator and exclusions

The output states its own denominator instead of leaving it implied:

- `snapshot` — requested limit, ordering, records returned, records usable, `truncated` (true when the adapter returned a full window, i.e. older records may exist), and the oldest/newest `created_at` actually seen.
- `excluded` — counts of `non_terminal` (pending/running/blocked and unknown statuses), `missing_task_type`, and `malformed` (non-object records). A malformed record is counted and skipped; it never aborts the cycle.
- One row per task is counted. Retries collapse into a task's attempt count and never inflate the denominator.

## Learning and anti-self-reinforcement rules

- No model-generated score is used to rank or authorize tasks.
- No automatic prompt, policy, budget, permission, provider, or tool changes.
- No memory writes. The current memory store is local SQLite and is not yet a verified shared cloud memory for remote scheduled runs.
- No scorecard is considered reliable below five terminal outcomes, and no escalation is issued from a point estimate alone.
- Scorecards describe execution reliability only. A future quality score requires persisted independent reviewer evidence and acceptance-check results.
- Every observation is traceable to the task ledger snapshot and can be recomputed.
- The first release intentionally has no automatic promotion or tuning; future changes must be proposed, evaluated against a frozen regression set and a separate holdout set, reviewed, versioned, and rollback-capable.

## Side-effect contract

This command performs zero task-ledger writes, claims, queue reordering, provider/model calls, external writes, budget changes, permission changes, or production operations. It does not start a workflow or alter the scheduler.

One honest caveat: `store()` falls back to `TaskStore(db_path())`, whose initialiser creates its parent directory and applies `CREATE TABLE IF NOT EXISTS` plus `PRAGMA journal_mode=WAL`. On a fresh machine, therefore, the command materialises the local SQLite ledger and its WAL files. That is a local schema bootstrap, not a ledger mutation, and it does not occur on the remote path.

## Known limitations and next increments

- The task adapter returns a bounded recent window (100 remote, 200 local) ordered newest first. Scorecards describe that window; `snapshot.truncated` says whether older records may exist. There is no server-side total count to compare against yet.
- There is no independent reviewer-result field in the current task outcome contract, so semantic quality cannot yet be measured.
- This cycle is invoked explicitly or by a separately approved existing workflow; it does not add a new cron.
- A later stage may persist a compact cycle record through an existing approved durable store after schema, retention, idempotency, and privacy review.
- A later stage may add model-assisted planning only after a provider's free quota, commercial terms, data handling, account eligibility, and hard stop are verified. If no eligible route exists, deterministic monitoring remains functional.
