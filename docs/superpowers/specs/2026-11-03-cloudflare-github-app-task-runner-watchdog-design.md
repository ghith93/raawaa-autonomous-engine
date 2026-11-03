# Cloudflare-to-GitHub Task Runner Watchdog

## Intent

Restore dependable bounded Task Runner invocation when GitHub's best-effort schedule event stops firing. Keep the current Python worker, D1 task ledger, Cloudflare control Worker, and GitHub Actions workflow as the canonical execution path. Use the existing Cloudflare Cron Trigger as a watchdog that dispatches the existing workflow_dispatch event only when no recent Task Runner run of any trigger type, no active run, and no durable dispatch intent exists.

## Why this change

- The last verified schedule-triggered Task Runner run is #41532657423 at 2026-11-02 21:55 UTC.
- PR #283 changed the Task Runner cron phase, but no subsequent Task Runner schedule event has been observed.
- Cloudflare's separate 15-minute Cron is running and its D1 heartbeat was re-verified at 2026-11-03 01:00:04.433 UTC.
- A manual re-run of the bounded Task Runner completed successfully at 01:06 UTC, read D1 status with HTTP 200, and processed zero tasks (queue_idle). This proves the runner works when invoked, not that its schedule works.
- The GitHub connector can inspect and modify repository files/PRs, but it exposes no workflow_dispatch action and cannot read secret values.

## Goals

1. Add an optional Cloudflare Cron watchdog that checks the Task Runner workflow's recent runs.
2. If a queued/running Task Runner exists, do nothing.
3. If any Task Runner invocation on main was created within the last 10 minutes, do nothing; the watchdog is a fallback, not a second primary schedule.
4. Otherwise dispatch only ghith93/raawaa-autonomous-engine/.github/workflows/task-runner.yml on main.
5. Authenticate with a GitHub App installation token scoped to this single repository and Actions: write.
6. Generate short-lived installation tokens in memory; never persist, print, or log them.
7. Fail closed when any required App setting is missing or invalid.
8. Preserve the existing 15-minute stale-lease recovery Cron and D1 task ledger.
9. Keep the feature dormant until the operator configures the three Cloudflare Worker secrets.

## Non-goals

- No replacement of GitHub Actions, Python Task Runner, D1, or the current queue.
- No new scheduler framework, Durable Object, database, or package dependency.
- No model inference, provider routing, live trading, Telegram activation, or customer-facing action.
- No GitHub PAT or broad personal-account token.
- No arbitrary repository, branch, workflow, or API target supplied by task payload.
- No attempt to enable a manually disabled GitHub workflow through an administrative endpoint.

## Approaches considered

1. Rely on GitHub schedule only. No new credentials or code, but the current failure mode remains unresolved. Rejected as the sole scheduler.
2. Cloudflare Cron plus a fine-grained PAT. Simple to implement, but creates a long-lived personal credential with more lifecycle and account-risk than necessary. Rejected.
3. Cloudflare Cron plus a GitHub App installation token. One-time App setup; token can be restricted to this repository and Actions write permission, and expires after one hour. Recommended. The watchdog only dispatches when GitHub's scheduled runner is stale, so the existing schedule remains primary when healthy.

## Design

### Cron behavior

- Preserve the existing recovery schedule: */15 * * * *.
- Add a watchdog schedule: 1,6,11,16,21,26,31,36,41,46,51,56 * * * * (two minutes after the primary Task Runner tick).
- Branch on the exact controller.cron string.
- Keep the watchdog's early return when App configuration is absent. Missing configuration must cause zero network calls and zero D1 writes.
- The two Cron expressions are deliberately offset from each other and from the GitHub Task Runner schedule.

### GitHub App configuration

Cloudflare Worker settings:

- GITHUB_APP_ID
- GITHUB_APP_INSTALLATION_ID
- GITHUB_APP_PRIVATE_KEY

The GitHub App must be installed only on ghith93/raawaa-autonomous-engine and granted only Actions: write. The installation-token request must further restrict the token to repository ID 1441352567 and Actions: write.

The user configures these values directly in Cloudflare Worker secrets. Never ask the user to paste the private key into chat. Do not store the key in GitHub source or GitHub Actions artifacts.

### Authentication

- Build a GitHub App JWT with RS256, iat = now - 60s, exp = now + 540s, and iss = GITHUB_APP_ID.
- Support both GitHub's downloaded PKCS#1 PEM (BEGIN RSA PRIVATE KEY) and PKCS#8 PEM (BEGIN PRIVATE KEY) by wrapping PKCS#1 DER in a PKCS#8 PrivateKeyInfo envelope before Web Crypto import.
- Exchange the JWT for an installation token at POST /app/installations/{installation_id}/access_tokens.
- Request only the single repository ID and Actions: write.
- Keep the token only in memory for the duration of the watchdog invocation.

### Staleness and dispatch

Use the GitHub REST workflow-runs endpoint for the fixed workflow filename and main branch:

GET /repos/ghith93/raawaa-autonomous-engine/actions/workflows/task-runner.yml/runs?branch=main&per_page=20

- If any returned run is queued, in_progress, waiting, or requested, return active_run.
- If any Task Runner run's created_at is no more than 10 minutes old, return recent_task_runner_run.
- Otherwise call POST /repos/ghith93/raawaa-autonomous-engine/actions/workflows/task-runner.yml/dispatches with { "ref": "main" }.
- Accept documented success responses 200 and 204.
- Do not follow URLs from GitHub API responses or task payloads.
- Do not include response bodies, authorization headers, JWTs, private keys, or installation tokens in logs.

### Audit and failure handling

- Atomically reserve a sanitized D1 dispatch-intent row before the GitHub side effect; record a second sanitized audit event after an accepted dispatch.
- Keep the 10-minute intent window even if the post-dispatch audit write fails, so a later watchdog tick cannot immediately issue a duplicate request.
- On missing configuration, return disabled without network or D1 access.
- On API/auth/configuration failure, expose only a stable error code and HTTP status; do not echo response body or exception text.
- Let Cloudflare Cron report a failed invocation for real dispatch errors so the failure is visible in Cron Events.
- Do not treat a dispatch request as proof that the Task Runner completed; verify the resulting workflow_dispatch run separately.

## Safety and cost

- No new subscription or paid API.
- GitHub App permission is limited to Actions write on one repository.
- Token is short-lived and further repository-scoped.
- Only one fixed workflow and main ref can be dispatched.
- The existing workflow concurrency group and D1 atomic task claiming remain authoritative.
- Live inference remains disabled, policy remains safe_dry_run, and external writes remain disabled.
- No D1 migration is required.

## Acceptance criteria

1. Unit tests cover PEM parsing/wrapping, JWT signing/claims, token scoping, missing settings, active-run suppression, recent Task Runner run suppression for any event type, atomic D1 intent deduplication, stale-run dispatch, and sanitized failure behavior.
2. Worker tests prove the watchdog Cron does not run stale-lease recovery and the recovery Cron does not call GitHub.
3. All existing Python, Worker, D1, and Workflows tests pass.
4. Exact-head GitHub Actions CI passes before merge.
5. The merged Worker is deployed only through the existing reviewed deployment workflow.
6. Before secrets are configured, the deployed watchdog performs no network calls and no D1 reads or writes.
7. Before dispatch, an atomic D1 `task_runner_dispatch_intent` insert reserves a 10-minute dedupe window; a failed outcome audit cannot immediately cause a duplicate external dispatch.
8. After App setup, a real watchdog-triggered workflow_dispatch run is observed and the Task Runner completes its bounded batch.
9. A successful dispatch is not reported as proof of task execution; both stages require separate evidence.

## Rollback

Remove the watchdog Cron expression from wrangler.toml and revert the watchdog handler/import. The existing 15-minute recovery Cron, D1 ledger, and GitHub Task Runner workflow remain unchanged. Delete the three Cloudflare App settings if the integration is abandoned.

## External setup boundary

The current ChatGPT GitHub connector cannot create a GitHub App or read its private key, and the Cloudflare connector is not available. The implementation can be merged and deployed dormant, but automatic dispatch cannot activate until the owner creates/installs the narrowly scoped GitHub App and adds the three settings in Cloudflare.
