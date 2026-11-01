# D1 control Worker

## Scope

This Worker is a narrow authenticated control-plane API. It does not expose raw SQL,
model inference, task execution, arbitrary operation calls, or customer-facing writes.

- `GET /health`: public read-only D1 health check.
- `GET /v1/tasks?limit=50`: authenticated bounded task listing (1–100).
- `POST /v1/tasks/enqueue`: authenticated, idempotent task intake with bounded JSON payloads.

All task writes use prepared statements and a D1 batch so task insertion and its audit
event are committed together. Duplicate idempotency keys return the existing task.
The Worker returns generic storage errors and does not leak SQL, bindings, or credentials.

## Configuration and deployment

The D1 binding and database identifier are declared in `wrangler.toml`. The Worker
requires a separate `ENGINE_API_TOKEN` secret; do not reuse the Cloudflare account
API token as an application bearer token.

Before deployment:

1. Apply `migrations/0001_initial.sql` to the configured D1 database using the
   reviewed manual migration workflow.
2. Set `ENGINE_API_TOKEN` as a Worker secret using Wrangler.
3. Deploy with Wrangler using the repository's Cloudflare API token.

No secret is stored in source control. Do not enable public enqueue routes without
setting the Worker secret. Keep task execution, external writes, and model inference
disabled until the D1 worker/runner lifecycle is separately implemented and tested.

## Tests

Run `npm run test:worker` with Node.js 20+. Tests use an in-memory mock binding and
do not connect to or mutate the real Cloudflare database.
