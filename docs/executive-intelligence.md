# Executive Intelligence v1

## Purpose

Provide a deterministic, explainable management view over the existing task ledger before introducing autonomous planning or task dispatch. This is an additive control-plane capability, not a replacement for the task store, workflow planner, memory, quota ledger, or specialist roster.

## Implemented in v1

The `engine.executive_intelligence` module and `python -m engine.cli executive-status` command provide a read-only snapshot over the existing `TaskStore` or configured `RemoteTaskStore` adapter:

- Counts tasks by lifecycle state.
- Ranks pending tasks using a deterministic, explainable score based on task-type impact, age, and prior attempts.
- Highlights failed and blocked work.
- Flags running tasks with missing or expired leases.
- Produces a small next-action recommendation list.
- Reports explicit zero side effects: no task creation, claim, queue reorder, network request, or model call.

The score is decision support only. It does not modify the underlying FIFO claim order and does not authorize task execution. Unknown task types remain visible with a neutral base weight instead of being silently dropped.

## Safety and architectural boundaries

- Reuses the existing task ledger and adapter selected by `engine.cli.store()`.
- No new database, table, queue, scheduler, provider, dependency, credential, or agent runtime.
- No changes to task lifecycle, claim semantics, lease fencing, retries, or approval gates.
- No model inference and no network calls.
- Does not interpret task payload text as policy or executable instructions.
- All actions requiring external writes, spending, publication, production deployment, access changes, or deletion remain behind existing approval gates.

## Scoring model

Each pending task receives:

- 70% of its task-type base weight.
- Up to 20 age points (10 per 24 hours).
- Up to 10 retry points (5 per prior attempt).

Initial task-type weights are explicit in `TASK_PROFILES`. They are provisional operational triage weights, not learned model scores. They must be evaluated against real task outcomes before tuning. The snapshot includes the method and rationale so an operator can challenge its ranking.

## Run locally

```bash
python -m engine.cli executive-status
python -m engine.cli executive-status --limit 5
```

The command prints JSON and reads the most recent tasks through the existing adapter (up to 200 locally; the remote D1 adapter currently caps the response at 100). It does not claim that the returned priority order is the actual queue claim order.

## Next stages

1. **Portfolio state:** reconcile GitHub PRs/issues, exact-head CI, scheduled workflow health, and task-ledger state into a single timestamped read model. Do not duplicate the durable source records.
2. **Strategic objectives:** define versioned objectives, measurable outcomes, dependencies, owners, and review dates using the existing structured memory and task contracts where suitable.
3. **Planning and delegation:** generate bounded task proposals with acceptance checks, cost estimates, risk, dependencies, and rollback; require deterministic validation before enqueue.
4. **Independent verification:** reviewer role and test evidence must be distinct from the executor's completion claim.
5. **Continuous operations:** scheduled snapshots, failure recovery, stale-state alerts, and dashboard readback through existing Actions/Worker infrastructure.

Each stage must reuse existing infrastructure, remain zero-cost by default, and pass exact-head CI. Do not claim continuous autonomous operation until durable scheduling, successful non-idle task execution, recovery, and monitoring are demonstrated end to end.
