# D1 control Worker

## Scope

This Worker is a narrow authenticated control-plane API. It does not expose raw SQL,
model inference, task execution, arbitrary operation calls, or customer-facing writes.

- `GET /health`: public read-only D1 health check.
- `GET /v1/tasks?limit=50`: authenticated bounded task listing (1–100).
- `POST /v1/tasks/enqueue`: authenticated, idempotent task intake with bounded JSON payloads.
- `POST /v1/tasks/claim`: atomically claims the oldest pending task and issues a server-generated 15-minute lease token.
- `POST /v1/tasks/:id/heartbeat`: extends a still-valid lease by 15 minutes.
- `POST /v1/tasks/:id/finish`: completes a leased task as `succeeded`, `failed`, or `blocked`.
- `POST /v1/tasks/:id/retry`: releases a leased task back to `pending` while attempts remain, otherwise marks it `failed`.

All task writes use prepared statements and a D1 batch so task insertion and its audit
event are committed together. Duplicate idempotency keys return the existing task.
The Worker returns generic storage errors and does not leak SQL, bindings, or credentials.

## Configuration and deployment

The D1 binding and database identifier are declared in `wrangler.toml`. The Worker
requires a separate `ENGINE_API_TOKEN` secret; do not reuse the Cloudflare account
API token as an application bearer token.

Deployment lifecycle:

- `.github/workflows/deploy-worker.yml` deploys automatically after reviewed changes to `worker/` or `wrangler.toml` reach `main`; it can also be dispatched manually for the initial deployment.
- It installs the dedicated `ENGINE_API_TOKEN` as a Worker secret from the GitHub Actions secret of the same name, then deploys with Wrangler.
- The D1 schema is now applied automatically by `.github/workflows/d1-auto-migrate.yml` after migration changes are merged to `main`.

One-time setup required before the first deployment: create a strong, random application bearer token and add it as the repository Actions secret `ENGINE_API_TOKEN`. Never reuse `CLOUDFLARE_API_TOKEN`. The deployment workflow checks that this secret exists and stops before deployment if it is missing. The Cloudflare API token must have Workers Scripts write permission in addition to the D1 permissions already used by migrations.

No secret is stored in source control. Do not enable public enqueue routes without
setting the Worker secret. Keep task execution, external writes, and model inference
disabled until the D1 worker/runner lifecycle is separately implemented and tested.

## Tests

Run `npm run test:worker` with Node.js 20+. Tests use an in-memory mock binding and
do not connect to or mutate the real Cloudflare database.
