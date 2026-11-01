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
