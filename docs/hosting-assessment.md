# Hosting assessment: bounded scheduler and durable state

Assessment date: 2026-11-01

## Decision

Do not deploy the current SQLite-file runtime as an always-on service on a free PaaS.
The next viable zero-cost direction to prototype is a split architecture:

1. GitHub Actions runs the existing Python engine in bounded batches on a schedule.
2. Cloudflare D1 stores durable task, memory, operation-ledger, and audit state.
3. A small Cloudflare Worker can expose a narrowly scoped authenticated enqueue/health API
   and/or dispatch work, but must not be treated as a general-purpose compute host.
4. The worker remains dry-run until the D1 adapter, lease semantics, operation ledger,
   policy gates, and end-to-end tests are proven.

This is a prototype direction, not a production deployment approval. No account resources,
secrets, paid plans, or external integrations were created or enabled by this assessment.

## Evidence and trade-offs

### Cloudflare Workers + D1

Cloudflare's current documentation lists Workers Free with 100,000 requests/day,
128 MB memory, and 10 ms CPU per invocation; the account includes up to five Cron
Triggers. Cron triggers run in UTC. D1 is available on Free and Paid plans, with
5 GB total storage, 5 million rows read/day, and 100,000 rows written/day on Free.

This makes Workers useful for a small control plane and D1 suitable for durable
structured state at prototype scale. It does not mean an agent process can run
continuously or perform heavy CPU work inside a free Worker invocation. The existing
engine uses Python sqlite3 and local SQLite files; it cannot be pointed at D1 as if
D1 were a mounted SQLite file. A dedicated D1 repository adapter and schema migration
are required. Python Workers support bindings including D1, but the adapter must use
the Workers D1 binding API and be tested in the actual runtime.

Official references:
- https://developers.cloudflare.com/workers/platform/limits/
- https://developers.cloudflare.com/workers/configuration/cron-triggers/
- https://developers.cloudflare.com/d1/platform/pricing/
- https://developers.cloudflare.com/workers/languages/python/
- https://developers.cloudflare.com/d1/worker-api/

### Render Free

Render Free web services spin down after 15 minutes without inbound traffic and
have an ephemeral filesystem. Local SQLite data is lost on spin-down/redeploy/restart.
Free Postgres expires after 30 days. This is not an acceptable durable always-on
home for the current SQLite engine.

Official reference:
- https://render.com/docs/free

### GitHub Actions

GitHub Actions is appropriate for CI and bounded scheduled execution, not for a
persistent daemon. Hosted job filesystems are temporary; state must live in a
separate durable database. Scheduled jobs can be delayed and should be designed
to tolerate missed or overlapping invocations. The batch runner added in PR #19
provides admission caps but does not solve persistence or scheduling by itself.

Official references:
- https://docs.github.com/en/actions/using-workflows/events-that-trigger-workflows#schedule
- https://docs.github.com/en/actions/using-github-hosted-runners/about-github-hosted-runners

## Required engineering work before deployment

1. Implement a storage interface and a Cloudflare D1 adapter for tasks, operation
   ledger, memory, and audit events. Preserve transaction/claim fencing semantics;
   verify D1 transaction behavior rather than assuming local SQLite behavior.
2. Add schema migrations and export/import tooling for the existing SQLite database.
3. Add an authenticated, least-privilege Worker endpoint or use a narrowly scoped
   API path for the scheduled runner. Never expose raw SQL or arbitrary operation calls.
4. Configure GitHub Actions schedule with bounded `run-batch` limits, overlap control,
   and explicit failure reporting. Make missed schedules harmless.
5. Add integration tests against a disposable D1 database and prove stale-lease,
   duplicate-claim, idempotency, reconciliation, and recovery behavior.
6. Add quota/cost alarms and a hard kill switch before any model inference.
7. Keep all customer-facing writes, publishing, commerce actions, and deployment
   behind policy approval. Do not store secrets in repository files or workflow logs.

## Provisioning gate

Before implementation can be validated end-to-end, the owner must have a Cloudflare
account with Workers Free available and create a D1 database. Deployment requires a
D1 database identifier and a narrowly scoped Cloudflare API token stored as GitHub
Actions secrets (never committed). The current repository tools do not expose a
Cloudflare account/database provisioning action, so no account resource was created.

No paid plan is required for the proposed prototype. If the required Free resources
or permissions are unavailable, stop and reassess rather than enabling billing.

## Current status

- Bounded Python batch runner: implemented and merged.
- Persistent remote storage adapter: not implemented.
- Cloudflare D1 database: not provisioned.
- Scheduled deployment: not configured.
- Live model inference and external actions: disabled.
