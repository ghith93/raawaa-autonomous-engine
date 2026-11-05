# Durable and unattended execution — options, decision, and what is proven

**Status:** decision made and evidenced; one option implemented and locally
proven; nothing deployed, nothing written to production D1.
**Tasks:** `CHATGPT-ENGINE-RUNTIME-003` (issue #464). **Spend:** USD 0.
**Supersedes:** nothing. **Depends on:** `docs/process-supervisor.md`.

## The gap, stated exactly

`engine/supervised_task.py` refuses to run a supervised task when the task store
has no local `db_path`:

```python
db_path = getattr(store, "db_path", None)
if supervisor is None and db_path is None:
    store.finish(task_id, "blocked", error="Supervised execution requires a local task store …")
```

That is fail-closed and correct, and it has a price: the **only** scheduled
consumer the project has — `.github/workflows/task-runner.yml`, every five
minutes — uses `RemoteTaskStore` against
`raawaa-autonomous-engine-control.ghith-shikhkhaled.workers.dev`, which is the
**production** control Worker and its production D1. A GitHub-hosted runner's
filesystem is destroyed when the job ends, so a `process_dispatches` row written
there is not durable across runs. On that host, the at-most-once guarantee the
supervisor provides has nowhere to live.

The gap is therefore precise: **where does the dispatch record live when the
worker is ephemeral and the queue is remote?**

## Measured facts about what already exists

| Fact | Evidence |
|---|---|
| The scheduled runner is five-minute cron and already authenticates to the production control Worker | `.github/workflows/task-runner.yml` (`cron: "2,7,…,57 * * * *"`, `ENGINE_API_URL=https://raawaa-autonomous-engine-control.ghith-shikhkhaled.workers.dev`) |
| That runner does **not** set `RAAWAA_ENGINE_PROCESS_SUPERVISOR`, so supervised tasks are blocked there today | same file: only `RAAWAA_ENGINE_LIVE_INFERENCE: disabled` is set |
| The remote queue already has lease fencing with expiry enforcement | `engine/remote_task_store.py:176` `heartbeat()` → `POST /v1/tasks/{id}/heartbeat` |
| A durable, lease-fenced, at-most-once **step identity** already exists in D1 | `migrations/0007_task_model_invocations.sql`; `worker/task-invocations.js` |
| Its states are `prepared / in_flight / ambiguous / settled`, unique per `(task_id, step_key)` | same migration (`UNIQUE(task_id, step_key)`) |
| Every mutation is conditioned on a live lease *inside the same statement* | `worker/task-invocations.js` — each `UPDATE … WHERE … AND EXISTS (SELECT 1 FROM tasks WHERE … lease_token=? AND lease_expires_at>=?)` |
| It stores **no** classification, exit code or truncation flag | columns are `step_key, task_kind, request_id, status, last_route_id, timestamps` |
| It has **no** reconcile/stale-sweep action | actions are only `prepare, start, reject, ambiguous, settle` |
| A local D1 emulation exists, so Worker SQL can be exercised without a remote write | `package.json` → `test:d1-local`, `test:workflow-local` |
| Storage on a GitHub-hosted runner is not durable | GitHub's documented runner model; also the reason the repository's own `docs/bounded-batch-worker.md` warns against putting SQLite on an ephemeral runner |

## The three options

### (a) Durable D1 dispatch ledger + remote queue/lease integration

Put the dispatch record in the same D1 database as the task and the lease, and
reuse the **existing** invocation route instead of building a second one.

- **What it costs:** nothing. D1 and the Worker route are already deployed and
  already exercised by Staging CI on every merge.
- **What it needs:** a Python adapter implementing the supervisor's
  `DispatchLedger` protocol over `POST /v1/tasks/{id}/invocations`, with
  `step_key = process-dispatch:<dispatch_id>` and `task_kind =
  process_supervision`. `begin` = `prepare` + `start`; `settle` = `settle`;
  `quarantine` = `ambiguous`.
- **What it cannot do, and this is the honest part:** the invocation table
  records *identity and lifecycle*, not *outcome*. A replayed `settled`
  invocation therefore cannot report whether the process succeeded. The only
  sound reading is "it ran and the outcome is not recoverable" — which maps to
  `ambiguous`, not to `succeeded`. A crash in the microseconds between `settle`
  and writing the task result costs one ambiguous task result. That is the same
  trade the supervisor already makes everywhere else, and it is the fail-closed
  direction.
- **Second limitation:** there is no remote stale sweep. An `in_flight`
  invocation whose worker died blocks that dispatch identity permanently until
  an operator resolves it. Safe (never replays), but not self-healing.

### (b) A genuinely persistent isolated runner with a durable local ledger

A host that outlives the job: the Codespaces bridge, or any long-lived VM.

- **What it costs:** unknown, and *unknown is not zero*. Codespaces free
  allowances, idle-stop behaviour and their effect on a running supervisor are
  account limits this project has never verified. Recording them as "free"
  would be exactly the kind of unfounded claim the standing directive forbids.
- **What it conflicts with:** `docs/sandboxed-execution-decision.md` (issue #440)
  already rejected a long-lived credential-bearing VM as the place to practise
  containment. Re-adopting one to obtain a durable SQLite file would invert that
  decision for an unrelated reason.
- **What it needs:** a persistent host we do not have, plus credential
  placement on it, plus an availability story.

### (c) Another supported runtime

Temporal, or Cloudflare Durable Objects/Workflows.

- **What it costs:** Temporal is a server + worker deployment — a second
  control plane next to the D1 ledger, which the standing directive tells us not
  to add without measured evidence that the existing design cannot work.
  Cloudflare Workflows is a spike in this repository
  (`tests/cloudflare_workflows_local_smoke.mjs`), not a binding in
  `wrangler.toml`, and its limits under this account are unverified.
- **Verdict:** more moving parts than the problem requires, for a durability
  property the existing D1 table can already express.

## Decision

**Option (a), in its least-complex form: reuse the existing invocation route
rather than adding a `process_dispatches` table and a new ingress path.**

Reasons, in order of weight:

1. It is the only option that puts dispatch identity next to the lease it
   depends on, in one atomic statement, in one database. A separate durable
   store would have to be reconciled against that lease forever.
2. It needs **no** Worker change, **no** migration and **no** new public route,
   so it adds no ingress surface and no schema risk. The security review's
   standing concern about new public paths simply does not apply.
3. Its failure mode is `ambiguous`, which the supervisor already defines as
   "never retry, investigate". Nothing has to be re-taught.
4. Everything about it can be tested locally, so this slice stays inside "no
   production D1 writes, no deployments, no paid use".

Explicitly **not** done, and why:

- No new D1 table, because the existing one already encodes the property.
- No Worker-side reconcile action, because a wrong reconcile is a replay, and a
  replay is the one thing this whole subsystem exists to prevent.
- No scheduled enablement of `RAAWAA_ENGINE_PROCESS_SUPERVISOR` on the
  production Task Runner, because that is a production behaviour change and
  this slice does not authorize one.

## What is proven now: crash recovery on a durable target

New test: `tests/test_worker_crash_recovery.py`. It is the first evidence in
this repository that survives an *uncooperative* death:

1. A real OS process (`tests/fixtures/supervised_worker_crash.py`) claims a task
   from a real SQLite file, records a dispatch, and starts a real child process.
2. The test waits until the child genuinely exists (read from `/proc`), then
   sends the worker `SIGKILL`. No handler, no `finally`, no atexit.
3. Observed state: the task is `running` with no result, the dispatch row is
   `in_flight` with no classification, and **the supervised child is still
   alive** — it is in its own session, which is both the containment boundary
   and the orphan risk in the same fact.
4. The test kills the orphan, expires the lease the way time would, and starts
   a **second, fresh worker**.
5. The second worker recovers the lease (`{"requeued": 1}`), claims the task as
   attempt 2, and is refused: `classification: ambiguous`,
   `reason: dispatch_already_in_flight`, `replayed: true`, no process started.
6. The task ends `blocked` at attempt 2; the dispatch row is still `in_flight`;
   the operation ran exactly once.

That is the restart/crash path the previous increment could not show, on a
target that genuinely outlives the process that wrote it. It is **local**
evidence: a file on a disposable machine, not D1, not a deployment.

## What is still not proven

1. **No remote durability yet.** The D1-backed dispatch ledger is decided, not
   implemented. Until it is, a supervised task on the scheduled runner is
   refused by design.
2. **No unattended schedule.** Nothing enqueues `supervised_probe`. The
   five-minute runner would process one only if it were enqueued, and the switch
   is off there.
3. **No remote crash proof.** The crash test proves the local ledger and the
   lease-recovery path. Proving the D1 path needs a real interrupted remote run,
   which this slice is not authorized to create.
4. **No operator tooling** for an `in_flight` invocation that no worker owns.
5. Still not a sandbox, still no network isolation, still no `setsid` escape
   defence.

## Rollback

Nothing was enabled. The crash test is a test; the fixture is test-only. Revert
the commit and the state returns to the previous increment. No deployment, no
migration, no secret, no permission, no production D1 write.
