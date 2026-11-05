# Durable and unattended execution — options, decision, and what is proven

**Status:** local crash recovery is implemented and proven; the remote durability
architecture remains unimplemented. The initial reuse proposal is rejected by the
Runtime-003 part 2 compatibility review below. Nothing deployed or written to
production D1.
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

### (a) Reuse the existing D1 model-invocation route — rejected

The existing route already provides lease-fenced identity and useful at-most-once
transitions, so it initially appeared to be the smallest option. The Runtime-003
part 2 compatibility review below found it is not safe to use for subprocesses:
it has no immutable operation fingerprint, no outcome fields, no atomic result +
task completion action, and it applies model-specific route-rejection retry
semantics. Do not build the proposed Python adapter on this route. A separate,
process-scoped D1 record/action contract is the revised direction. Its isolated
D1 cost/quotas and deployment must be verified before remote execution is enabled.

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
- **Current status:** deferred. A dedicated D1 table and narrow authenticated
  actions appear simpler and more directly aligned with process dispatch than
  introducing a second runtime, but no account-specific quota/deployment
  verification or remote execution proof exists.

## Initial decision — superseded by the compatibility review below

The initial option-(a) proposal to reuse the existing invocation route was
reconsidered in Runtime-003 part 2. That route cannot persist and atomically
bind a subprocess fingerprint/outcome to task completion; see the verified
incompatibilities below. Do not implement the original reuse proposal.

These were the original reasons for preferring reuse, but they are not
sufficient: sharing a lease fence does not supply the process-specific identity
and result contract. The compatibility review below supersedes this proposal.

Explicitly **not** done, and why:

- No process-specific D1 table or route was added in the initial slice. The
  compatibility review below shows that the model invocation table does not
  encode the full subprocess identity/outcome property; the separate table is
  now the recommended direction, not a rejected option.
- No Worker-side reconcile action was added. A wrong reconcile can replay work;
  uncertain started dispatches must remain quarantined for investigation.
- No scheduled enablement of `RAAWAA_ENGINE_PROCESS_SUPERVISOR` on the
  production Task Runner; the switch remains off pending isolated validation.

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

## Runtime-003 part 2 — compatibility review and revised decision

**Review result (2026-11-05): the current model-invocation API is not a safe
subprocess dispatch ledger.** The earlier option-(a) suggestion to directly
reuse `/v1/tasks/{id}/invocations` is withdrawn. The route has useful lease
fencing and at-most-once identity primitives, but its contract is specifically
a logical model-call lifecycle and cannot prove a supervised process outcome.
No remote dispatch adapter has been implemented or enabled.

Verified incompatibilities in the current code:

| Required subprocess guarantee | Existing invocation contract | Consequence |
|---|---|---|
| Bind one stable per-task/step identity to an immutable operation fingerprint | `task_model_invocations` stores `step_key` and `task_kind`, but no argv/operation fingerprint. `task_kind` only rejects a different string; it does not validate what operation was actually launched. | Reusing the route without changing its contract cannot detect a task retry whose operation differs under the same dispatch identity. Putting a digest in `task_kind` would overload a model-domain field rather than provide a typed, reviewed contract. |
| Record subprocess outcome evidence | `settle` records only `status='settled'` and timestamps; there is no classification, exit code, signal, timeout/cancellation state, or truncation metadata. | `settled` cannot mean process success. A process result may not be inferred from invocation status. |
| Atomically bind durable result to completion under the current lease | Invocation settle and `/v1/tasks/{id}/finish` are distinct requests. `finish` stores the task result and clears the lease; the invocation transition does not store that result. | Either order has a crash/response-loss gap: settle first can leave a settled row without result, while finish first ends the lease and prevents a subsequent fenced invocation transition. No existing API operation atomically writes both. |
| Represent process execution without model-route retry semantics | Invocation start requires a `route_id`; the lifecycle supports returning to `prepared` only after a recorded explicit route rejection, then starting another route. | A synthetic process route would borrow model-specific retry rules and misrepresent subprocesses in model invocation metrics. The route must not be broadened for this use. |

The local D1 smoke at `tests/d1_local_worker_smoke.mjs` verifies the current
model route behavior (prepared replay, distinct steps, route start/reject,
settle, ambiguity and replay rejection). It does **not** demonstrate the missing
subprocess fingerprint/outcome contract, true concurrent D1 dispatch safety,
or remote crash recovery.

### Revised smallest safe direction (design only)

If implementation proceeds, add an explicitly process-scoped D1 ledger and
narrow authenticated Worker actions instead of widening the model endpoint.
The record needs a stable unique `(task_id, step_key)` identity, a validated
supervisor/operation version plus immutable operation fingerprint, and a
bounded versioned outcome record. Each state-changing action must include the
active task lease in its own conditional D1 statement/batch:

`prepared -> in_flight -> settled(outcome)`

`prepared -> in_flight -> ambiguous(reason)`

The `start` transition must commit before launching the child. A lost response
or lease after start, a timeout without confirmed termination, an unknown
outcome, or a conflicting fingerprint must never authorize replay. Settled
replays are acceptable only when the submitted outcome exactly matches the
already-persisted versioned result; they are not permission to launch again.
The route must validate `task_type=supervised_probe`, keep the existing
`execution_mode` inference meaning unchanged, bound and redact stored output,
and expose metadata-only aggregate monitoring. This is a proposed design, not
implemented or verified against D1. Keep the scheduled supervisor switch off.

## What is still not proven

1. **No remote durability yet.** A process-scoped D1 dispatch ledger is only a
   design recommendation. Until implemented and validated, a supervised task on
   the scheduled runner is refused by design.
2. **No unattended schedule.** Nothing enqueues `supervised_probe`; the
   five-minute runner switch is off.
3. **No remote crash proof.** The crash test proves only local SQLite behavior.
   Proving the D1 path needs isolated Staging tests and interrupted-run recovery;
   neither has been done.
4. **No operator tooling** for an orphaned/ambiguous process dispatch.
5. Still not a sandbox, still no network isolation, still no `setsid` escape
   defence.

## Rollback

Nothing was enabled. The crash test is a test; the fixture is test-only. Revert
the commit and the state returns to the previous increment. No deployment, no
migration, no secret, no permission, no production D1 write.
