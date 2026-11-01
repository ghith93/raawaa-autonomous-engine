# Runtime v1: task queue and model routing

## Delivered in this phase

- SQLite task persistence with unique idempotency keys.
- Atomic claim of the next pending task using a SQLite write transaction.
- Explicit task states and append-only audit events.
- Bounded task listing and audit listing.
- Dry-run worker for a small allowlist of task types.
- Provider-neutral model selection with free-only default and no network implementation.
- Unit tests for queue, worker, and routing behavior.
- Scheduled GitHub Actions configuration audit every six hours.

## Important operating limits

The SQLite database lives on the runner filesystem when run in GitHub Actions. GitHub-hosted runners are ephemeral, so this workflow does **not** create durable cross-run task storage. Do not use this as a production queue until a persistent volume/database is configured.

The scheduled workflow only runs checks. It does not keep a process alive, call AI models, or execute autonomous business operations. GitHub Actions schedules can be delayed or disabled by GitHub inactivity policies.

## Local usage

```bash
python -m engine.cli enqueue --type research_brief --title "Research agent frameworks" --payload '{"topic":"open-source agent frameworks"}'
python -m engine.cli list
python -m engine.cli run-once
python -m engine.cli audit-log
```

The default database path is `data/engine.db`. Set `RAAWAA_ENGINE_DB` to override it.


## Failure recovery update

The SQLite task store now supports bounded retry and stale-running recovery:

- A worker exception moves a task back to `pending` while `attempts < max_attempts`.
- Once the attempt budget is exhausted, the task becomes `failed`.
- A worker invocation checks for stale `running` records (default lease age: 15 minutes) before claiming new work.
- Recovery decisions are written to the audit log.
- Only the exception class is persisted by the worker; exception text is intentionally omitted because it may contain prompts, credentials, or customer data.
- Unsupported task types remain `blocked` and are not retried.

**Important limitation:** the current worker only creates dry-run tickets and has no external side effects. Stale lease recovery must not be used for long-running external actions until lease heartbeats, ownership tokens, and idempotent side-effect handling are implemented. A timeout alone cannot prove that an old worker has stopped.
