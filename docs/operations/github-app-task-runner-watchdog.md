# GitHub App setup for the Task Runner watchdog

## Current status — read this before quoting the rest of the page

**The fallback dispatch is active and verified — evidence below, not assertion.**

- Readiness: the authenticated `/v1/mobile/status` read in Task Runner run
  `41774549441` (04:39:56 UTC) reported `task_runner_watchdog: ready` and
  `missing settings: none`.
- Dispatch: run [`41775727949`](https://github.com/ghith93/raawaa-autonomous-engine/actions/runs/41775727949)
  was created at 04:51:42 UTC with event `workflow_dispatch` and actor
  `raawaa-engine[bot]`, two seconds after the Worker recorded its intent at
  `2026-11-04T04:51:40.456Z`. Nothing human or agent triggered it.
- Coverage: the Worker deployed at 10:53:38 UTC from `c906b3e` dispatches six
  workflows rather than one (see Coverage below).

Without the switch the handler returns `disabled` with `reason:
missing_configuration` before any D1 access or network call; that fail-closed gate
is unchanged and tested.

The activation value is declared in `wrangler.toml` `[vars]` as
`GITHUB_APP_WATCHDOG_ENABLED = "enabled"`, and `tests/test_deployment_config.py`
pins both that declaration and its absence from `wrangler.staging.toml`. It was
dashboard-managed once; it went missing, the deploy still uploaded, and the only
symptom was a fallback that silently never dispatched. A non-secret switch that
decides whether a safety net is armed belongs in the reviewed diff.

Two consequences that are still easy to get wrong:

1. A configured cron is not a working dispatch. Listing the five-minute expression
   in `wrangler.toml` proves scheduling, not activation; the readiness read is the
   evidence, and it is now taken automatically after every deploy.
2. The 15-minute recovery pass touches **D1 tasks only**. It does not ingest GitHub comments and does not recover missed issue or PR activity.

## Purpose

The Cloudflare Worker has two independent scheduled responsibilities:

- Every 15 minutes: bounded stale-lease recovery in D1.
- Every five minutes on an offset schedule: check whether GitHub's Task Runner schedule is stale and, only if necessary, dispatch the existing Task Runner workflow. **Active and verified — see Current status above.**

The watchdog is a fallback. It does not replace the GitHub schedule or the Python Task Runner. It skips dispatch when a Task Runner run is queued/running, when any Task Runner run on main was created within the previous 10 minutes (regardless of trigger type), or when a durable D1 dispatch intent was reserved within the previous 10 minutes.

## Disabled-by-default behavior

The watchdog requires three Cloudflare Worker secrets and one explicit non-secret activation variable. If any secret or the activation variable is missing, the handler returns disabled before any D1 access or network request.

- GITHUB_APP_ID
- GITHUB_APP_INSTALLATION_ID
- GITHUB_APP_PRIVATE_KEY

Historically you had to add the non-secret Cloudflare Worker variable GITHUB_APP_WATCHDOG_ENABLED with the exact value enabled only after verifying the GitHub App installation, repository scope, and all three secrets. This is a separate activation switch; the App secrets alone do not start the watchdog. The value is now declared in `wrangler.toml` `[vars]`, so it travels with the reviewed deploy; a value set in the dashboard is preserved by Wrangler's `--keep-vars` flag but is no longer the source of truth. The deployment verification must fail if `task_runner_watchdog.ready` is false.

The private key is never needed in ChatGPT and must never be pasted into a conversation, issue, pull request, log, or source file.

## Coverage — six workflows, not one

The watchdog originally recovered only `task-runner.yml`. A dropped `schedule`
event produces **no failure notification**, so every other scheduled workflow had
exactly the same silent failure mode. The five-minute dispatch cron now covers
all of them, each with its own staleness threshold derived from its own cadence
plus slack (`worker/task-runner-watchdog.js`, `WATCHED_WORKFLOWS`):

| Workflow | GitHub cron | Stale after |
|---|---|---|
| `task-runner.yml` | `2,7,… * * * *` | 10 min |
| `maintenance.yml` | `17 */6 * * *` | 6 h 30 m |
| `research-discovery.yml` | `31 5 * * *` | 25 h |
| `cex-public-market-smoke.yml` | `17 7 * * *` | 25 h |
| `v3-live-readonly-smoke.yml` | `17 4 * * *` | 25 h |
| `groq-connection-check.yml` | `17 10 * * 1` | 7 d 12 h |

Three properties matter more than the table:

1. **Thresholds are cadence plus slack, never shorter.** The watchdog recovers a
   *missed* run; it must not become a second schedule layered on a working one.
2. **Each workflow has its own intent event.** A shared intent key would let one
   maintenance dispatch suppress the task runner for six hours, which is a silent
   outage disguised as a safety check.
3. **One workflow's failure cannot stop the others.** A broken run-list read for
   the weekly Groq check is no reason to stop recovering the queue; the failure is
   returned *and* written to `audit_log` as `<label>_dispatch_failed`, because a
   silently swallowed dispatch failure is the original bug wearing a new coat.

`getWatchdogCoverage()` exposes the covered workflows and thresholds so the
readiness surface can report what is actually protected rather than implying it.

The one path that is still *not* covered: nothing here dispatches for a
workflow that is missing from `WATCHED_WORKFLOWS`. Adding a workflow to
`.github/workflows` does not add it to the fallback; that is a deliberate
decision, made in code, by someone who reads this file.

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

At that time the `GITHUB_APP_WATCHDOG_ENABLED=enabled` opt-in was an operator-managed Cloudflare variable, deliberately kept out of `wrangler.toml`; that policy was reversed on 2026-11-04 after the dashboard value went missing and silently disarmed the fallback. It is now declared in `wrangler.toml` `[vars]`. The current deployed code returns `disabled` before D1 or network access unless all three App secrets and that exact opt-in variable are present.

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

Remove the watchdog Cron expression from wrangler.toml and revert the watchdog handler/import through a reviewed pull request. To disable without a code change, set `GITHUB_APP_WATCHDOG_ENABLED` to anything other than `enabled` in the dashboard; the watchdog immediately returns disabled. To disable durably, change it in `wrangler.toml` through a reviewed pull request. Keep the 15-minute stale-lease recovery Cron intact.


## Deployment configuration regression — 2026-11-03 21:43 UTC

Production deployment run [#41723367620](https://github.com/ghith93/raawaa-autonomous-engine/actions/runs/41723367620) succeeded, but the subsequent Task Runner mobile-status read in [#41724349887](https://github.com/ghith93/raawaa-autonomous-engine/actions/runs/41724349887) reported `watchdog_ready=not_ready` and `watchdog_missing=GITHUB_APP_WATCHDOG_ENABLED`. The deployment log had warned that the dashboard variable differed from `wrangler.toml` and would be overridden. The immediate post-deploy probe reported ready, but the later independent read is the current evidence and shows the activation variable is missing.

The corrective deployment contract is:

- Use `wrangler deploy --config wrangler.toml --keep-vars` to preserve dashboard-managed variables while keeping secrets in Cloudflare's secret store.
- Fail the deployment verification if `task_runner_watchdog.ready` is false; a successful Worker upload alone is not a healthy release.
- Do not add the GitHub App private key or other secret values to Wrangler configuration. The activation *switch* is not a secret and now lives in `wrangler.toml`; secrets stay in Cloudflare's secret store.
