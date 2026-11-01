

## Quota-gated worker inference

The worker accepts an optional, programmatically constructed `QuotaAwareLiveGateway`. The default CLI and scheduled Task Runner do not construct or inject one, so the normal execution path remains a deterministic zero-network dry run. Live execution is considered only when the operator switch `RAAWAA_ENGINE_LIVE_INFERENCE=enabled` is present **and** a gateway has been explicitly injected. The gateway independently requires its programmatic network flag, a task ID, and at least one currently verified eligible provider profile with atomic per-task/daily/monthly request and token reservations.

The worker sends the task title and JSON payload to the selected provider only in that explicitly enabled path. Treat queued payloads as untrusted input and do not enqueue secrets, passwords, private customer records, or other sensitive data. The live request is bounded to 256 output tokens. Provider/gateway failures are terminally blocked for that attempt rather than automatically retried, because a timeout or transport failure may mean the provider already processed the request. Lease heartbeat and ownership fencing remain required before result persistence. No provider profile or credential is shipped/configured by this change; therefore this wiring alone does not enable live inference or change scheduled jobs from dry-run mode.
