# Durable and unattended execution — options, decision, and what is proven

**Status:** local SQLite crash recovery is proven, and a process-scoped remote
D1 ledger/Worker route/Python adapter is implemented and locally tested on PR
#540. It has not been deployed or verified against Staging D1; nothing was
written to production D1. The initial model-invocation reuse proposal is rejected
by the compatibility review below.
**Tasks:** `CHATGPT-ENGINE-RUNTIME-003` (issue #464). **Spend:** USD 0.
**Supersedes:** nothing. **Depends on:** `docs/process-supervisor.md`.

## The gap, stated exactly

Before Runtime-003 part 2, `engine/supervised_task.py` correctly refused a
remote store because it had no durable dispatch ledger. The current PR adds a
separate D1-backed process ledger, but the only scheduled consumer —
`.github/workflows/task-runner.yml`, every five minutes — still targets the
**production** control Worker and does not set `RAAWAA_ENGINE_PROCESS_SUPERVISOR`.
That switch remains off. A GitHub-hosted runner's filesystem is destroyed when
the job ends, so a local `process_dispatches` row could not survive across runs;
the new adapter instead writes to the remote D1 table. This implementation has
only been exercised against the local D1 emulation, not Staging or production.

The gap is therefore precise: **does the D1-backed identity/outcome lifecycle
remain safe across actual remote leases, worker termination, and scheduled
recovery?**

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

- Runtime-003 part 1 did not add a process-specific D1 table or route. This
  Runtime-003 part 2 PR now adds migration `0013_task_process_dispatches` and a
  dedicated authenticated Worker route, because the model invocation table does
  not encode the full subprocess identity/outcome property.
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
The original compatibility proposal was withdrawn. PR #540 now implements a
separate adapter and route, but neither is deployed or enabled in any scheduled
runner.

Verified incompatibilities in the current code:

| Required subprocess guarantee | Existing invocation contract | Consequence |
|---|---|---|
| Bind one stable per-task/step identity to an immutable operation fingerprint | `task_model_invocations` stores `step_key` and `task_kind`, but no argv/operation fingerprint. `task_kind` only rejects a different string; it does not validate what operation was actually launched. | Reusing the route without changing its contract cannot detect a task retry whose operation differs under the same dispatch identity. Putting a digest in `task_kind` would overload a model-domain field rather than provide a typed, reviewed contract. |
| Record subprocess outcome evidence | `settle` records only `status='settled'` and timestamps; there is no classification, exit code, signal, timeout/cancellation state, or truncation metadata. | `settled` cannot mean process success. A process result may not be inferred from invocation status. |
| Atomically bind durable result to completion under the current lease | Invocation settle and `/v1/tasks/{id}/finish` are distinct requests. `finish` stores the task result and clears the lease; the invocation transition does not store that result. | Either order has a crash/response-loss gap: settle first can leave a settled row without result, while finish first ends the lease and prevents a subsequent fenced invocation transition. No existing API operation atomically writes both. |
| Represent process execution without model-route retry semantics | Invocation start requires a `route_id`; the lifecycle supports returning to `prepared` only after a recorded explicit route rejection, then starting another route. | A synthetic process route would borrow model-specific retry rules and misrepresent subprocesses in model invocation metrics. The route must not be broadened for this use. |

`tests/d1_local_worker_smoke.mjs` now exercises the model route separately and
the new process route against a real local Worker with Wrangler's local D1:
concurrent duplicate prepare, fingerprint mismatch, start-once, restart after
prepared/in-flight/settled, immutable bounded result, concurrent task claims,
stale-lease fencing and no-replay quarantine. It also runs
`tests/fixtures/remote_process_dispatch_smoke.py`, which uses the Python adapter
against that local Worker and starts the allowlisted platform probe process.
This is local D1 emulation evidence, not Cloudflare Staging or production D1
concurrency. The Python adapter unit tests simulate a lost response after start
and confirm a subsequent lease quarantines rather than starts again.

### Revised implementation (local only; Staging verification pending)

PR #540 adds `migrations/0013_task_process_dispatches.sql`,
`worker/task-process-dispatches.js`, and `engine/remote_process_dispatch_ledger.py`.
The identity is unique on `(task_id, step_key)` with a stable process step key,
operation version, a full SHA-256 operation fingerprint over the trusted argv
digest, and a lease fingerprint (never the lease token). The Worker accepts only
the existing `supervised_probe` task type and fixed read-only operation names.
Every insert/update is conditional on the active, unexpired task lease in its
D1 statement. `start` commits before the child is spawned. Settled records carry
version-1 classification, exit code, signal, timeout/cancellation booleans,
truncation, bounded stdout/stderr, duration and descendant metadata.

`prepared` may safely resume because it proves no start transition committed.
An `in_flight` record observed under a later valid task lease is atomically
quarantined as `ambiguous`; `settled` replays return the persisted result. A
lost start response never authorizes a child launch. A lost settlement
response leaves the current client ambiguous; a later delivery reads a durable
settled outcome and replays it without spawning. Exact repeated `settle` requests
are idempotent only when their serialized outcomes match. Remote stale
reconciliation is intentionally unsupported: ambiguous work is non-replayable
and operator recovery tooling remains future work. The adapter is integrated
only for `RemoteTaskStore`; no local `db_path` is fabricated.
`tasks.execution_mode` remains an inference label, not process execution mode.

Local checks currently pass: 66 focused supervisor/adapter tests, 206 Worker
tests, and the local D1 integration smoke including a real local child. Exact PR
CI and isolated Staging verification are still pending. The scheduled supervisor
switch remains off; no Staging deployment, production deployment, production
D1 write, inference, or spend occurred.

## What is still not proven

1. **No isolated Staging D1 proof yet.** The adapter is implemented on PR #540
   and tested with local D1 emulation; it has not been deployed to or exercised
   against isolated Staging D1.
2. **No unattended schedule.** Nothing enqueues `supervised_probe`; the
   production five-minute runner switch is off. No Staging-specific scheduled
   runner has been added.
3. **No remote crash proof.** Local tests cover Worker restart boundaries and
   simulated start-response loss, but no real runner was killed against Staging
   D1 between each transition.
4. **No operator tooling** for an orphaned/ambiguous process dispatch or safe
   human resolution.
5. Still not a sandbox, still no network isolation, still no `setsid` escape
   defence.

## Rollback

Nothing was enabled. The crash test is a test; the fixture is test-only. Revert
the commit and the state returns to the previous increment. No deployment, no
migration, no secret, no permission, no production D1 write.
