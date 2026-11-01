# Cloudflare Workflows durability spike

**Status:** isolated prototype; not deployed and not connected to production D1, queues, AI bindings, provider credentials, or the production Worker entrypoint.

## Purpose

Test whether Cloudflare Workflows can replace part of the engine's custom orchestration/retry/checkpoint burden while preserving D1 as the eventual authoritative task, idempotency and audit ledger.

The prototype uses deterministic fake adapters only:
1. claim task with an idempotency key and lease expiry;
2. research stub;
3. review stub;
4. checkpoint marker;
5. optionally wait for an external approval event;
6. completion marker.

It deliberately does not call a model, write D1, send messages, publish content, or mutate customer-facing systems.

## Files

- `workflow-core.js`: strict payload validation, deterministic workflow ID, durable step sequence and bounded retry policy.
- `worker.js`: local-only test endpoints for create/status/approve/cancel/restart.
- `wrangler.toml`: isolated Workflow binding, no public routes, no production bindings, concurrency limit 2 and one-day retention.
- `../../tests/cloudflare_workflows_spike.test.js`: deterministic unit tests with a checkpointing mock.
- `../../tests/cloudflare_workflow_local_smoke.mjs`: local Wrangler runtime smoke test.

## Run locally

From the repository root with Node 20+ and network access to download the pinned Wrangler CLI:

```bash
npm run test:workflow-spike
npx --yes wrangler@4.156.0 --version
npm run test:workflow-local
```

The local smoke test starts Wrangler on `127.0.0.1:8791`, with a filtered environment that omits Cloudflare/provider/GitHub credential variables. It tests duplicate create, approval events, restart from a named step, cancellation, expired leases, and approval timeout.

## Security and operational boundaries

- **Do not deploy this spike.** It is not a production API or a secured approval endpoint.
- `workers_dev = false`, no routes, no account ID, no secrets, no D1, no Queues, no AI, no R2 and no production bindings are configured.
- Local Wrangler uses the Workers/Miniflare runtime. Cloudflare warns that this is not a hardened sandbox for untrusted code; run only trusted repository code in this local environment.
- Workflow event payloads and step results must remain small, serializable and non-sensitive. The production design should pass task IDs and bounded references, not prompts, secrets, customer data or large result bodies.
- The workflow retry policy is deliberately small (two retries, exponential backoff). Every future side-effecting step must use an idempotency key and must be reviewed for replay safety.
- The one-day retention setting is intentionally below the Free-plan maximum and reduces stored state.

## Adoption gate

Do not integrate this into the production Worker until the spike passes:
- local Wrangler runtime smoke tests;
- duplicate create and event-delivery tests;
- restart/replay tests proving completed steps are reused;
- cancellation, timeout, lease-expiry and terminal-failure tests;
- a cost/limit review against current Cloudflare plan limits;
- a comparison with the current Microsoft Agent Framework checkpoint adapter and GitHub Actions runner.

If adopted, D1 remains the source of truth; Workflows is the durable orchestration layer only.
