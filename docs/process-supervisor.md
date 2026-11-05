# Bounded process supervisor

**Status:** implemented, locally tested (real child processes) and reachable
from a real task, **off by default**. Not deployed and not an OS sandbox.
Tasks: `CHATGPT-ENGINE-RUNTIME-001` (supervisor) and
`CHATGPT-ENGINE-RUNTIME-002` (task-lifecycle integration). Cost of both
increments: **USD 0** — no provider, no network call, no deployment, no
production write, no permission or secret change.

## The gap this closes

The engine could already *decide* to do work, but it could not *own* a process
doing it:

- `engine/batch_runner.py` states the limitation itself: its time budget is an
  **admission** budget, because a synchronous task that has already started
  cannot be interrupted.
- `engine/codespace_bridge.py` runs repository commands as a child of the
  bridge, with the bridge's uid, and a `subprocess.run(timeout=...)` that leaves
  grandchildren running.
- `engine/agent_runtime.py` is a deterministic contract and never calls a model.
- `engine/sandbox_policy.py` describes the *boundary* (gVisor, no network,
  read-only rootfs) but explicitly says wall-clock enforcement and process-group
  termination are somebody else's job.

`engine/process_supervisor.py` is that somebody. It is the smallest thing that
turns "run this operation" into an owned, bounded, recorded, interruptible
child process — without adding a second orchestrator, queue, database or vector
store.

## The contract, piece by piece

| Property | How it is enforced |
|---|---|
| Only known programs run | `OperationRegistry` holds `OperationSpec` entries written in trusted code. A task names an operation; it cannot name a path. |
| No shell, ever | A shell (`sh`, `bash`, `dash`, `zsh`, `pwsh`, …) is refused as an allowlisted executable at construction time. |
| No caller-supplied code | An `ArgSlot` may not sit behind an interpreter's code flag (`-c`, `-e`, `--eval`, `-Command`). Parameters are values, not programs. |
| Fixed argument schema | Each slot declares a regex applied with `re.fullmatch`; NUL, newlines and non-matches are refused. Unknown and missing parameters are refused. |
| No inherited credentials | The child environment is *constructed*, not copied. Only `ENV_ALLOWLIST` plus `HOME`/`TMPDIR`/`CI`-style names exist. `PYTHONPATH` is absent by design. An allowlisted value that still has a credential shape is refused rather than passed. |
| Whole-tree ownership | `start_new_session=True` makes the child its own process-group leader, so `killpg` reaches every descendant that did not deliberately escape. |
| Hard wall clock | A monotonic deadline, not an admission budget. On expiry: `SIGTERM` to the group, `grace_seconds` to comply, then `SIGKILL`. |
| Descendant cleanup after the leader exits | If the leader exits while its children still hold the stdout/stderr pipes, the pipes are the evidence: the group is `SIGKILL`ed so the readers can finish and nothing survives the run. |
| Bounded output | Each stream keeps at most `output_bytes`; past 16× that the reader closes its end, so an infinite writer hits `EPIPE` instead of filling memory. |
| Redacted output | Every captured stream passes through `engine.redaction.redact` before it can be recorded, printed or committed. |
| Disposable filesystem anchor | The child's `HOME`, `TMPDIR` and default working directory are a `tempfile.mkdtemp()` deleted when the run ends. |
| Lease fence before | Ownership is renewed and confirmed **before** the dispatch record is written and before the process starts. A failed fence leaves no record, so a legitimate retry is still possible. |
| Lease fence after | Ownership is confirmed again after the child exits. A lost fence makes the outcome `ambiguous`, never `succeeded`. |
| Cancellation on lease loss | `LeaseWatchdog` renews from inside the wait loop; when renewal fails the child is terminated and the run is classified `cancelled`. |
| At-most-once dispatch | A dispatch id derived from task + operation + parameters + supervisor version is claimed in SQLite before the spawn. A duplicate delivery gets the recorded outcome; an interrupted one is quarantined, never replayed. |

## Outcome classification

| Classification | Meaning | Retry? |
|---|---|---|
| `succeeded` | Exit code 0, and the result was recorded. | No — it is done. |
| `failed` | Non-zero exit or a fatal signal, recorded. | Only with new parameters or a new dispatch key. |
| `timed_out` | The wall clock expired and the tree was killed. | Only after understanding why it overran. |
| `cancelled` | The caller or the lease asked to stop. | After the lease problem is resolved. |
| `refused` | Nothing ran: bad operation, bad schema, lost fence before start, spawn failure, or a refused environment. | Yes; a refusal has no side effect, and it is recorded so a repeat gets the same answer. |
| `ambiguous` | The process ran but the result cannot be attributed — lease lost afterwards, or the ledger could not be settled. | **No.** Investigate. This is exactly the case where a blind retry duplicates an external effect. |

## The dispatch ledger

`process_dispatches` is one additional table in the **existing** engine SQLite
database (`TaskStore`'s database file), not a new store:

```
dispatch_id PRIMARY KEY, task_id, operation, argv_digest,
lease_fingerprint (sha256 prefix, never the token), status,
classification, exit_code, output_truncated, detail, created_at, updated_at
```

- `begin()` is `INSERT OR IGNORE` plus a read, so two racing workers produce
  exactly one insert and the loser learns what the winner was doing.
- A row in `in_flight` with no outcome is what a crash looks like.
  `reconcile(stale_after_seconds)` quarantines rows older than the window; the
  default of 900 s keeps a concurrent supervisor's fresh rows out of reach.
- The ledger stores an **argv digest**, never the argv, and a lease
  **fingerprint**, never the lease token.
- Output is deliberately *not* durable. A replayed success therefore returns the
  recorded classification and exit code with no stdout: claiming the output
  would mean inventing it.

## Task-lifecycle integration

`engine/supervised_task.py` connects the supervisor to the queue that already
exists, so the chain is real rather than a diagram:
`enqueue → claim (lease) → fence → supervise → classify → result → audit`.
It adds no second lifecycle: the task row, lease, retries, audit log and
idempotency key all remain `TaskStore`'s.

**The switch.** `RAAWAA_ENGINE_PROCESS_SUPERVISOR=enabled` is required for the
handler to do anything. Anything else — including unset — blocks the task with
`process_supervisor_disabled` before a dispatch is recorded or a process exists.
Turning the feature off is therefore always safe, everywhere, without a code
change.

**The worker allowlist is narrower than the registry.** A task payload may name
only `git_head_revision` or `python_platform_probe`. Both are parameter-free,
read-only, local and free. The payload must be exactly `{"operation": "…"}`;
a `parameters`, `executable` or `command` key is refused outright rather than
ignored, because those are the exact shapes a caller would use to widen what
the child receives.

**How a classification becomes a task status:**

| Supervisor classification | Task status | Why |
|---|---|---|
| `succeeded` | `succeeded` + bounded result | the outcome is recorded and replay-safe |
| `failed`, `timed_out` | `failed` | a re-dispatch would replay the recorded failure under the same identity, not retry the work |
| `refused` | `blocked` | nothing ran; the request itself is wrong |
| `ambiguous` | `blocked` | it ran and cannot be attributed — never `succeeded` |
| `cancelled` / lost lease | **nothing written** | the task stays `running` and `recover_stale()` owns it; writing after the lease is gone would race the worker that owns it now |

`tasks.execution_mode` keeps its existing meaning — inference. A supervised
read-only probe performs none, so the row stays `dry_run` and the result carries
`"execution": "supervised_process:v1"`. Conflating the two would quietly widen
what `live_inference` has always meant.

**Running one:**

```bash
python -m engine.cli enqueue --type supervised_probe --title "Commit probe" \
  --payload '{"operation":"git_head_revision"}' --key commit-probe-1
RAAWAA_ENGINE_PROCESS_SUPERVISOR=enabled python -m engine.cli run-once
```

Local evidence (`python -m unittest tests.test_supervised_task`): a real task
runs and records the commit, with `task_enqueued/task_claimed/task_heartbeat/
task_succeeded` in the audit log and one settled dispatch row; a timeout and an
interrupted dispatch both land as terminal non-success states; and a lease lost
mid-execution writes nothing at all and is requeued by `recover_stale()`.

## Verification

Local, with real processes (`python -m unittest tests.test_process_supervisor`):

| Proof | Test |
|---|---|
| Hard timeout kills a sleeping child | `test_hard_timeout_kills_the_process` |
| A spawned grandchild is dead after the run | `test_descendant_tree_is_killed_when_the_deadline_expires` |
| An orphan that keeps the pipes open is killed and the run returns in ~40 ms | `test_orphaned_descendant_is_killed_after_the_parent_exits` |
| 100 000 lines are cut to 2 048 bytes and flagged | `test_output_is_bounded_and_flagged` |
| A printed `Authorization: Bearer …` and `ENGINE_API_TOKEN=…` are redacted | `test_output_is_redacted_before_it_can_be_recorded` |
| `ENGINE_API_TOKEN`/`GITHUB_TOKEN` exist in the parent and not in the child | `test_child_inherits_no_secret_bearing_variable` |
| The child's `HOME` and cwd are a temp dir that no longer exists afterwards | `test_child_runs_in_a_disposable_home_and_directory` |
| A lost lease before start prevents execution and leaves no record | `test_lost_lease_before_start_prevents_execution_and_leaves_no_record` |
| A lost lease during a 30 s sleep cancels it in well under a second | `test_lease_loss_during_execution_cancels_and_kills_the_child` |
| A lost lease after the run yields `ambiguous` + `quarantined` | `test_lease_lost_after_execution_makes_the_outcome_ambiguous` |
| The same dispatch twice runs once | `test_duplicate_dispatch_returns_the_record_and_never_runs_twice` |
| An interrupted dispatch is ambiguous, then quarantined, and stays un-runnable | `test_an_interrupted_dispatch_is_quarantined_not_replayed` |
| A parameter cannot become the executable, and a shell cannot be registered | `test_a_parameter_cannot_choose_the_executable`, `test_a_shell_can_never_be_an_allowlisted_executable` |
| An expired lease in the real `TaskStore` blocks the fence and leaves no record | `test_an_expired_lease_in_the_real_task_store_blocks_the_fence` |
| The ordinary dry-run task lifecycle still works unchanged | `test_ordinary_task_lifecycle_still_passes_through_the_existing_worker` |

That is local evidence on Linux, under this repository's CI runner image. It is
**not** evidence of isolation, of behaviour on another host, or of production
operation — the supervisor has never been deployed.

## Using it

```bash
# What may the engine start? Starts nothing.
python -m engine.cli supervisor-operations

# The exact vector, limits and child variable *names*. Starts nothing.
python -m engine.cli run-operation --operation git_recent_subjects \
  --parameter count=3 --task-id local-probe

# Actually run it, recording the dispatch in the local SQLite ledger.
python -m engine.cli run-operation --operation git_recent_subjects \
  --parameter count=3 --task-id local-probe --confirm
```

The second `--confirm` call with the same task id replays the recorded outcome
(`"replayed": true`) and starts nothing.

Default allowlist: `git_head_revision`, `git_worktree_status`,
`git_recent_subjects`, `python_platform_probe`. All four are read-only, local,
deterministic and free. None of them is a general "run a command" door, and the
registry is expected to stay that way.

## Rollback

Nothing was deployed, scheduled, or migrated in production, so rollback is a
revert:

1. `git revert` the PR. `engine/process_supervisor.py`, the two CLI branches and
   the tests disappear; the `.gitignore` line is inert on its own.
2. The `process_dispatches` table is created **lazily**, only when a supervisor
   with a SQLite ledger is instantiated. Reverting leaves any local table behind
   as dead data in a local development database; nothing reads it.
3. `RAAWAA_ENGINE_PROCESS_SUPERVISOR` is unset in every environment, so no
   scheduler, workflow or deployment can reach the supervised task type even
   before a revert. Deleting the worker's two-line branch is sufficient to
   remove the capability entirely.
4. No secret, permission, ruleset, workflow, queue, or D1 state was touched, so
   there is nothing to un-grant and no container to drain.

## Remaining limits, stated rather than hidden

1. **Not a sandbox.** The child shares the uid, the filesystem and `/proc`. Real
   containment still requires `engine/sandbox_policy.py` under a throwaway
   runner.
2. **No network isolation.** A supervised process can open sockets. Only the
   sandbox contract (`--network=none`) removes that.
3. **Escaping the group is possible.** A descendant that calls `setsid` leaves
   the process group; the supervisor cannot see it. `PR_SET_CHILD_SUBREAPER` or
   a cgroup would be needed, and neither is implemented.
4. **One task type, off by default.** `supervised_probe` exists and is proven
   end to end, but nothing schedules it automatically and the switch defaults to
   off. A scheduler that enqueues it, and the evaluator stage that judges its
   output, are the next increments.
5. **`/proc` is Linux-specific.** Elsewhere the group scan returns nothing and
   cleanup falls back to `killpg`; descendant *counting* is then unavailable.
6. **One operation per call, one supervisor per call site.** There is no pool
   and no concurrency limit beyond what the caller imposes.
7. **Non-durable output.** Only classification, exit code and a truncation flag
   survive a replay.
8. **`failed` and `timed_out` are states, not judgements.** A task that needs to
   distinguish "the child crashed" from "the child was right to exit 1" must say
   so itself; the supervisor reports, it does not interpret.
