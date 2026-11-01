# D1 control Worker

## Scope

This Worker is a narrow authenticated control-plane API. It does not expose raw SQL,
model inference, task execution, arbitrary operation calls, or customer-facing writes.

- `GET /health`: public read-only D1 health check.
- `GET /v1/tasks?limit=50`: authenticated bounded task listing (1–100).
- `POST /v1/tasks/enqueue`: authenticated, idempotent task intake with bounded JSON payloads.
- `POST /v1/tasks/claim`: atomically claims the oldest pending task and issues a server-generated 15-minute lease token.
- `POST /v1/tasks/recover`: recovers up to 100 expired leases, requeueing tasks within their attempt budget and failing exhausted tasks.
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


## Python runner connection

The Python CLI uses local SQLite by default. To use D1 as the shared task queue, configure these environment variables on the runner:

- `RAAWAA_ENGINE_API_URL=https://raawaa-autonomous-engine-control.ghith-shikhkhaled.workers.dev`
- `RAAWAA_ENGINE_API_TOKEN`: the same application bearer token configured as the Worker secret; never commit or print it.

If the Worker is protected by Cloudflare Access, the runner can additionally use a narrowly scoped Access service token:

- `RAAWAA_ENGINE_CF_ACCESS_CLIENT_ID`: service-token Client ID.
- `RAAWAA_ENGINE_CF_ACCESS_CLIENT_SECRET`: service-token Client Secret.

For GitHub Actions, save these as repository secrets named `CF_ACCESS_CLIENT_ID` and `CF_ACCESS_CLIENT_SECRET`. Both are optional, but must be configured together. Create a service token in Zero Trust → Access controls → Service credentials → Service Tokens, then add it to the Access application policy for this Worker using the **Service Auth** action and the **Service Token** selector. Do not use an ordinary Allow policy for service-token authentication: strict service-token mode accepts Service Auth policies only. Do not disable Access or create a public bypass to make the runner work. The existing application Bearer token remains required after Access admits the request.

With `RAAWAA_ENGINE_API_URL` and `RAAWAA_ENGINE_API_TOKEN` present, `enqueue`, `list`, `run-once`, and `run-batch` use the authenticated D1 API. The local SQLite database continues to hold memory and model-usage records. Without the URL, the CLI uses SQLite for tasks. Remote audit-log listing is not exposed. The runner remains dry-run only and does not call models or external services.


## Scheduled lease maintenance

The Worker runs a bounded stale-lease recovery pass every 15 minutes using a Cloudflare Cron Trigger. Each invocation inspects at most 100 expired leases and requeues only tasks that have attempts remaining; exhausted tasks become failed. This is maintenance, not an AI agent loop: it does not execute task handlers, call models, or perform external writes.

The schedule is designed for the Cloudflare Workers Free limits published at implementation time: up to five Cron Triggers per account and 10 ms CPU per Cron invocation. See [Workers limits](https://developers.cloudflare.com/workers/platform/limits/) and [Cron Triggers](https://developers.cloudflare.com/workers/configuration/cron-triggers/). Cloudflare documents that trigger changes can take up to 15 minutes to propagate.


## Scheduled Python task consumer

GitHub Actions invokes the Python runner every 15 minutes and can also be started manually from the Actions tab. It uses the repository Actions secret `ENGINE_API_TOKEN`, the deployed Worker URL, and a strict batch limit of five tasks / 60 seconds. If Cloudflare Access is enabled for the Worker, it can additionally use the optional service-token secrets described above.

The runner currently supports only the safe dry-run handlers listed in `engine/worker.py`. Unsupported task types are marked blocked. It does not call Gemini, Groq, OpenRouter, or other model providers, and it cannot perform external writes. Scheduled GitHub Actions jobs may be delayed; this is periodic batch processing, not real-time or always-on execution.
