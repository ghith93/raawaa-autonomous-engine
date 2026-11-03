# GitHub App setup for the Task Runner watchdog

## Purpose

The Cloudflare Worker has two independent scheduled responsibilities:

- Every 15 minutes: bounded stale-lease recovery in D1.
- Every five minutes on an offset schedule: check whether GitHub's Task Runner schedule is stale and, only if necessary, dispatch the existing Task Runner workflow.

The watchdog is a fallback. It does not replace the GitHub schedule or the Python Task Runner. It skips dispatch when a Task Runner run is queued/running, when any Task Runner run on main was created within the previous 10 minutes (regardless of trigger type), or when a durable D1 dispatch intent was reserved within the previous 10 minutes.

## Disabled-by-default behavior

The watchdog requires all three Cloudflare Worker settings below. If any is missing, the handler returns disabled before making a network request or writing to D1.

- GITHUB_APP_ID
- GITHUB_APP_INSTALLATION_ID
- GITHUB_APP_PRIVATE_KEY

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
4. Add each of the three names above as a Secret. Paste the App ID and installation ID into their matching fields; paste the PEM contents only into GITHUB_APP_PRIVATE_KEY.
5. Save the settings. Do not send the private key to ChatGPT.

No GitHub Actions secret is required for this integration. No D1 migration is required.

## Verify activation

1. Wait for the next watchdog Cron event (the watchdog runs at minutes 1, 6, 11, 16, 21, 26, 31, 36, 41, 46, 51, and 56 UTC, two minutes after the primary Task Runner schedule).
2. If the GitHub schedule has resumed recently, the watchdog will skip dispatch. This is expected.
3. If no Task Runner run of any trigger type exists within the last 10 minutes, no active run exists, and no recent D1 dispatch intent exists, the Worker atomically reserves an intent and dispatches only task-runner.yml on main.
4. In GitHub Actions, verify a new Scheduled D1 Task Runner run whose event is workflow_dispatch.
5. Inspect the run's logs and bounded batch result. A successful dispatch request is not proof that the Task Runner completed.

If the dispatch fails, inspect Cloudflare Worker Cron Events for the sanitized failure code and HTTP status. A 403 usually indicates the App installation or Actions permission is incorrect. If the workflow has been manually disabled in GitHub, an owner must enable it in the Actions UI; the watchdog does not bypass that administrative state.

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
- Missing App settings cause a no-op. No recurring network calls are made until all three settings exist.

## Disable or roll back

Remove the watchdog Cron expression from wrangler.toml and revert the watchdog handler/import through a reviewed pull request. To disable without a code change, remove GITHUB_APP_PRIVATE_KEY from Worker secrets; the watchdog immediately returns disabled. Keep the 15-minute stale-lease recovery Cron intact.
