# Bounded batch worker

The batch runner is a bounded execution primitive for manually triggered or future
scheduled invocations. It does not create a persistent process, schedule itself,
or enable model inference or external writes.

## CLI

```bash
python -m engine.cli run-batch --max-tasks 5 --max-seconds 60
```

- Maximum 20 tasks per invocation.
- Maximum 300 seconds of task-admission time.
- Stops immediately when the queue is idle.
- Checks the time budget before admitting each next task.
- A synchronous task already running cannot be interrupted. The time limit is an
  admission budget, not a hard timeout for an individual task.
- Every task still passes through the existing worker and policy behavior. The
  current worker remains dry-run.

Do not treat this as a persistent scheduler. A later deployment must provide a
durable database shared across invocations, an external trigger, concurrency
controls, and monitoring. Do not put SQLite on an ephemeral runner filesystem
and expect state to survive between GitHub Actions jobs.
