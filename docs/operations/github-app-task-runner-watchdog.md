# GitHub App setup for the Task Runner watchdog

## Purpose

The Cloudflare Worker has two independent scheduled responsibilities:

- Every 15 minutes: bounded stale-lease recovery in D1.
- Every five minutes on an offset schedule: check whether GitHub's Task Runner schedule is stale and, only if necessary, dispatch the existing Task Runner workflow.

The watchdog is a fallback. It does not replace the GitHub schedule or the Python Task Runner. It skips dispatch when a Task Runner run is queued/running, when any Task Runner run on main was created within the previous 10 minutes (regardless of trigger type), or when a durable D1 dispatch intent was reserved within the previous 10 minutes.

## Disabled-by-default behavior

The watchdog requires three Cloudflare Worker secrets and one explicit non-secret activation variable. If any secret or the activation variable is missing, the handler returns disabled before any D1 access or network request.

- GITHUB_APP_ID
- GITHUB_APP_INSTALLATION_ID
- GITHUB_APP_PRIVATE_KEY

Also add the non-secret Cloudflare Worker variable GITHUB_APP_WATCHDOG_ENABLED with the exact value enabled only after verifying the GitHub App installation, repository scope, and all three secrets. This is a separate activation switch; the App secrets alone do not start the watchdog. The variable is intentionally not stored in wrangler.toml.

The private key is never needed in ChatGPT and must never be pasted into a conversation, issue, pull request, log, or source file.

## Create the least-privilege GitHub App

1. Open https://github.com/settings/apps/new while signed in as the repository owner.
2. Create a private GitHub App. A webhook URL is not required; disable webhook activation.
3. Under Repository permissions, grant only Actions: Read and write. Do not grant Contents, Administration, Issues, Pull requests, Secrets, or Organization permissions.
4. Under installation scope, choose Only select repositories and select only ghith93/raawaa-autonomous-engine.
5. Create the App and record its numeric App ID.
6. Generate one private key and keep the downloaded PEM file private.
7. Install the App on the selected repository and record the numeric installation ID from the installation settings page.

The Worker requests an installation token narrowed again to repository ID 1441352567 and Actions: write. The installation token is short-lived and exists only in Worker memory.

## Add settings in Cloudflare

1. Open the Cloudflare dashboard.
2. Go to Workers & Pages and select raawaa-autonomous-engine-control.
3. Open Settings, then Variables and Secrets.
4. Add GITHUB_APP_ID, GITHUB_APP_INSTALLATION_ID, and GITHUB_APP_PRIVATE_KEY as Secrets. Paste the App ID and installation ID into their matching fields; paste the PEM contents only into GITHUB_APP_PRIVATE_KEY.
5. Save the three Secrets. Add GITHUB_APP_WATCHDOG_ENABLED as a plain-text Worker Variable with the exact value enabled only after verifying the App installation and all three secrets. Do not send the private key to ChatGPT.

No GitHub Actions secret is required for this integration. No D1 migration is required.

## Production deployment status — 2026-11-03

The watchdog code was first deployed at 01:25 UTC from main SHA `4800d2aa3fb9dcd31c7dd0aaffe2e4d1b1b5638e` (Worker version `546a233f-5d93-4328-b71f-f3afed990b4c`). After PR #305 added the explicit `GITHUB_APP_WATCHDOG_ENABLED=enabled` opt-in, the production Worker was redeployed at 01:34 UTC from main SHA `15e0ff5c4051cd396a59e76dd9503db5448ff52a` (Worker version `b1cb1429-3914-412c-b681-20c245466269`). Deployment workflow [#41560533199](https://github.com/ghith93/raawaa-autonomous-engine/actions/runs/41560533199) completed successfully, and the public health endpoint returned `{"ok":true}`.

The explicit `GITHUB_APP_WATCHDOG_ENABLED=enabled` opt-in remains an operator-managed Cloudflare Worker variable and is deliberately not committed to `wrangler.toml`. The current deployed code returns `disabled` before D1 or network access unless all three App secrets and that exact opt-in variable are present.

The authenticated status read in Task Runner run [#41562069768](https://github.com/ghith93/raawaa-autonomous-engine/actions/runs/41562069768) at 01:48 UTC reported `not_ready`; all four required settings were missing: `GITHUB_APP_ID`, `GITHUB_APP_INSTALLATION_ID`, `GITHUB_APP_PRIVATE_KEY`, and `GITHUB_APP_WATCHDOG_ENABLED`. No dispatch request or intent is recorded. The GitHub connector cannot inspect Cloudflare secret values. Do not treat the fallback as active until the App is installed with least privilege, the three secrets and explicit opt-in variable are configured, and a real `workflow_dispatch` Task Runner run plus completed bounded batch are verified.

## Verify activation

1. Wait for the next watchdog Cron event (the watchdog runs at minutes 1, 6, 11, 16, 21, 26, 31, 36, 41, 46, 51, and 56 UTC, two minutes after the primary Task Runner schedule).
2. If the GitHub schedule has resumed recently, the watchdog will skip dispatch. This is expected.
3. If no Task Runner run of any trigger type exists within the last 10 minutes, no active run exists, and no recent D1 dispatch intent exists, the Worker atomically reserves an intent and dispatches only task-runner.yml on main.
4. In GitHub Actions, verify a new Scheduled D1 Task Runner run whose event is workflow_dispatch.
5. Inspect the run's logs and bounded batch result. A successful dispatch request is not proof that the Task Runner completed.

If the dispatch fails, inspect Cloudflare Worker Cron Events for the sanitized failure code and HTTP status. A 403 usually indicates the App installation or Actions permission is incorrect. If the workflow has been manually disabled in GitHub, an owner must enable it in the Actions UI; the watchdog does not bypass that administrative state.

## Latest production verification — 2026-11-03 02:56 UTC

The operator confirmed that all three GitHub App values were added as Cloudflare Worker Production secrets and then enabled the separate Production variable `GITHUB_APP_WATCHDOG_ENABLED=enabled`. The following evidence verifies the integration end to end:

- Task Runner run [#41569188010](https://github.com/ghith93/raawaa-autonomous-engine/actions/runs/41569188010) was created at 02:56:42 UTC on `main`, with event `workflow_dispatch` and actor `raawaa-engine[bot]`; it completed successfully at 02:56:58 UTC.
- Its authenticated D1 mobile-status probe returned HTTP 200, `watchdog_ready=ready`, `watchdog_missing=none`, and matching latest dispatch-request / D1-intent timestamps `2026-11-03T02:56:40.404Z`.
- The bounded batch completed with `tasks_processed=0` and `queue_idle`; no task was waiting, and no business task execution is claimed.
- The run reported the latest Worker stale-lease Cron heartbeat as `2026-11-03T02:45:34.076Z`, with zero leases requeued and zero failures.

This closes the one-time GitHub App setup and dispatch verification. Continue monitoring subsequent Worker Cron heartbeats and the primary GitHub `schedule` event separately. Do not interpret a successful idle dispatch as proof that a real task was processed or that GitHub's primary schedule is reliable.

## Security and operational guarantees

- No personal access token is used.
- The App is installed on one repository and granted Actions write only.
- Each installation token is restricted to this repository and Actions write.
- The Worker targets one hard-coded workflow and main branch; task payloads cannot choose a target.
- JWTs and installation tokens are not logged or stored.
- GitHub schedule remains primary when healthy; the watchdog is a fallback after a 10-minute stale threshold.
- A D1 dispatch-intent row is written before the GitHub side effect; it suppresses another attempt for 10 minutes even if the post-dispatch audit write fails.
- GitHub's workflow concurrency group and D1 atomic task claims remain the duplicate-execution controls.
- Live inference, external writes, trading, and Telegram delivery remain disabled.
- Missing App secrets or the explicit activation variable cause a no-op. No recurring GitHub API calls or D1 access occur until all four settings are present.

## Disable or roll back

Remove the watchdog Cron expression from wrangler.toml and revert the watchdog handler/import through a reviewed pull request. To disable without a code change, remove or change GITHUB_APP_WATCHDOG_ENABLED; the watchdog immediately returns disabled. Keep the 15-minute stale-lease recovery Cron intact.
