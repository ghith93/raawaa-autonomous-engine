# Hosting assessment: bounded scheduler and durable state

Assessment reconciled: 2026-11-02

## Decision

Keep the existing split architecture. Do not deploy the current SQLite-file runtime as an always-on service on a free PaaS.

1. GitHub Actions runs the existing Python engine in bounded batches on a schedule (currently every 15 minutes).
2. Cloudflare D1 stores the durable task lifecycle state used by the remote task adapter.
3. A Cloudflare Worker exposes the authenticated control API and D1 operations.
4. The normal task runner remains deterministic dry-run; live inference, paid routes, and external writes remain disabled.
5. The isolated Cloudflare Workflows spike is a local-only experiment. It is not deployed, has no production D1 binding, and is not a second queue consumer.

This is a bounded scheduled control plane, not a persistent always-on agent service. No paid plan or new billing is authorized by this assessment.

## Current architecture and verified boundaries

### Cloudflare Workers + D1

A Cloudflare control Worker with a D1 binding is deployed. The GitHub Actions Task Runner uses authenticated preflight and task lifecycle requests. The remote D1 adapter supports enqueue, list, claim, heartbeat, finish, retry, and stale-lease recovery.

D1 is the durable source of truth for remote task state. The adapter is not a mounted SQLite database: it uses the Worker API and D1 binding. The local SQLite task store remains the default when remote API configuration is absent.

The D1 adapter currently covers task lifecycle operations only. Project memory and model-usage accounting remain in local SQLite and are not automatically synchronized to D1. A future migration must preserve their schemas, idempotency, retention, and recovery semantics.

Workers are useful for a small control plane, not heavy or continuously running agent computation. Verify current account limits and billing semantics before adding bindings, Workflow instances, or any new resource. Do not assume public Free-plan ceilings guarantee account eligibility or zero cost for every operation.

Official references:
- https://developers.cloudflare.com/workers/platform/limits/
- https://developers.cloudflare.com/workers/configuration/cron-triggers/
- https://developers.cloudflare.com/d1/platform/pricing/
- https://developers.cloudflare.com/workers/languages/python/
- https://developers.cloudflare.com/d1/worker-api/
- https://developers.cloudflare.com/workflows/reference/limits/

### GitHub Actions

The scheduled D1 Task Runner is bounded and ephemeral. Each invocation runs a capped batch and exits; it is not a persistent process. Scheduled jobs can be delayed and should tolerate missed or overlapping invocations. D1 preserves queued task state between runs.

Official references:
- https://docs.github.com/en/actions/using-workflows/events-that-trigger-workflows#schedule
- https://docs.github.com/en/actions/using-github-hosted-runners/about-github-hosted-runners

### Render Free

Render Free web services spin down after 15 minutes without inbound traffic and have an ephemeral filesystem. Local SQLite data is lost on spin-down/redeploy/restart. Free Postgres expires after 30 days. This is not an acceptable durable always-on home for the current SQLite engine.

Official reference:
- https://render.com/docs/free

### Cloudflare Workflows prototype

PR #125 was merged as an isolated durability spike. Its deterministic fake-task workflow, approval wait/resume, replay, cancellation, timeout, and local runtime smoke tests passed exact-head CI. The prototype is not deployed and does not integrate D1, the production Worker, model providers, or customer-facing operations.

The prototype does not replace the current D1 Task Runner. Production adoption would require a separate reviewed design proving authenticated triggers, policy-loader enforcement, D1 claim/heartbeat/finish reuse, lease-token fencing, idempotent result persistence, cost/step budgets, and a single authoritative queue consumer. Production integration or deployment requires separate explicit approval.

## Required engineering work before broader deployment

1. Implement and test monetary spend/credit reservations before any non-zero-cost model route is considered.
2. Extend durable storage to memory and model-usage ledgers only after defining migration, backup, retention, and recovery requirements.
3. Preserve authenticated, least-privilege Worker endpoints; never expose raw SQL or arbitrary operation calls.
4. Keep GitHub Actions batch size, runtime, and overlap controls bounded; make missed schedules harmless.
5. Add integration tests against disposable D1 state for stale leases, duplicate claims, idempotency, reconciliation, and recovery.
6. Keep quota/cost alerts and hard kill switches ahead of any live inference.
7. Keep customer-facing writes, publishing, commerce actions, and production deployment behind operation-specific approval.

## Current status

- Cloudflare control Worker with D1 binding: deployed.
- Remote D1 task lifecycle adapter: implemented and exercised by the scheduled Task Runner.
- Local SQLite task store: retained as a development/default fallback.
- Project memory and model-usage ledgers: local SQLite; not migrated to D1.
- GitHub Actions D1 Task Runner: scheduled bounded batches; not an always-on worker.
- Cloudflare Workflows: isolated local prototype merged; not deployed or connected to production.
- Live model inference, paid routes, and external writes: disabled.
