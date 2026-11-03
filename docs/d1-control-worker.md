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

Deployment lifecycle (verified against the workflow files on 2026-11-03):

- `.github/workflows/deploy-worker.yml` is **manual-dispatch only**. It does not deploy on pushes or merges to `main`.
- The dispatch input `confirm_deployment` must equal `DEPLOY_CONTROL_WORKER`. The workflow checks `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`, and the dedicated `ENGINE_API_TOKEN` secret, installs the application bearer token as a Worker secret, then deploys the reviewed `main` revision with Wrangler.
- `.github/workflows/d1-migrate.yml` is also **manual-dispatch only**. Its `confirm_database` input must equal `raawaa-autonomous-e`; it first lists remote migration status and then applies reviewed migrations to that explicitly confirmed production D1 database.
- Neither Worker deployment nor production D1 migration is automatic after a merge. Treat them as separate production actions with separate explicit confirmations.

### Last verified production rollout snapshot — 2026-11-03 00:31 UTC

This is a read-only history snapshot, not authorization to repeat either production action:

- The latest observed successful `Deploy Control Worker` run in the current Actions history is [run #41546403591](https://github.com/ghith93/raawaa-autonomous-engine/actions/runs/41546403591). Its job log confirms it checked out main SHA `43b4f0d7ad2e3e63c10dd6f92fbebc00739610e7`, passed the explicit `DEPLOY_CONTROL_WORKER` confirmation, uploaded the existing `ENGINE_API_TOKEN` secret, and deployed Worker version `9d49bcfe-36b1-42e4-8882-902522c4e379` to the existing workers.dev endpoint.
- The latest observed successful production migration run is [run #41547859981](https://github.com/ghith93/raawaa-autonomous-engine/actions/runs/41547859981). Its remote preflight listed only `0004_paper_event_ledger.sql` and `0005_telegram_webhook_receipts.sql` as pending, and the apply step reported both successful. Migrations `0001`–`0003` were not pending at that check. The confirmed D1 database ID in the log is `f0ffbed3-940f-488d-a7d1-31cbc5fbf742`.
- The deployed Worker SHA predates PR #271's Worker Cron heartbeat implementation (main commit `263526278234e4680629ae841dd528e8f0299aa2`). The heartbeat code is merged on main but **not included in that observed production Worker version**. Do not infer that `worker_cron_tick` is being recorded until a separately authorized deployment and a subsequent authenticated status read confirm it.
- A later bounded Task Runner push run, [#41549379045](https://github.com/ghith93/raawaa-autonomous-engine/actions/runs/41549379045), successfully read the authenticated mobile status route (HTTP 200); paper-ledger, provider-usage and Telegram-receipt data were available, with zero pending/running tasks and `queue_idle`. It processed zero tasks. This confirms the route and listed data were working at that time, but does not verify the newer Worker Cron heartbeat or recurring schedule delivery.

Do not rerun deployment or migration workflows based on this historical record. The user has explicitly withheld authorization for further production deployment and trading; any future production action requires a new, action-specific approval.

One-time setup required before the first deployment: create a strong, random application bearer token and add it as the repository Actions secret `ENGINE_API_TOKEN`. Never reuse `CLOUDFLARE_API_TOKEN`. The deployment workflow checks that this secret exists and stops before deployment if it is missing. The Cloudflare API token must have Workers Scripts write permission in addition to the D1 permissions already used by migrations.

No secret is stored in source control. Do not enable public enqueue routes without
setting the Worker secret. The authenticated D1 task intake/claim/lease/finish lifecycle
and bounded Python consumer are implemented; supported task types currently execute
deterministic dry-run handlers only. Keep live model inference and external writes
disabled. Production Worker deployment, production migrations, Telegram webhook
configuration, and webhook registration remain separately gated actions.

## Tests

Run `npm run test:worker` with Node.js 20+. Tests use an in-memory mock binding and
do not connect to or mutate the real Cloudflare database.

The Python suite also applies all ordered D1 migrations to a temporary SQLite database and executes the exact quota reservation SQL from `worker/quota.js` under concurrent writers, after a database reopen, and with an active cooldown. These local parity tests validate SQL shape and regression behavior only; they do not prove Cloudflare D1 production concurrency, deployed schema state, or remote restart recovery. A non-production D1 test database remains required before enabling live inference.


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

GitHub Actions is **configured** with a five-minute cron and can also be started manually from the Actions tab. A configured cron is not proof of reliable periodic execution: GitHub scheduled events are best-effort and may be delayed or dropped. As of 2026-11-03 00:15 UTC, the latest verified `event=schedule` run was #20 at 2026-11-02 21:55 UTC; later runs #22 and #23 were `push` events, not scheduled runs. Periodic five-minute execution therefore remains unverified. The workflow uses the repository Actions secret `ENGINE_API_TOKEN`, the deployed Worker URL, and a strict batch limit of five tasks / 60 seconds. If Cloudflare Access is enabled for the Worker, it can additionally use the optional service-token secrets described above.

The runner currently supports only the safe dry-run handlers listed in `engine/worker.py`. Unsupported task types are marked blocked. It does not call Gemini, Groq, OpenRouter, or other model providers, and it cannot perform external writes. Scheduled GitHub Actions jobs may be delayed; this is periodic batch processing, not real-time or always-on execution.
