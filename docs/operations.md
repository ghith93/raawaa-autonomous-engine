# Operations and contributor guide

## Permanent autonomous operating directive

The [Master Autonomous Execution Directive](master-autonomous-execution-directive.ar.md) is permanent repository policy. Every agent and cycle must read it with the [Continuous Improvement Charter](continuous-improvement-charter.md), [Architecture](architecture.md), and applicable policies. A user message consisting only of `.` means resume from the latest verified repository state, execute the highest-priority safe work, and continue. See [AGENTS.md](../AGENTS.md) for startup instructions. The Python worker now enforces policy loading through `engine.operating_policy.load_operating_policy()` before every task claim. It reads the complete required documents, validates them, and supplies a compact trusted policy summary with SHA-256 fingerprints before task payload in the live model prompt. Missing or invalid policy blocks the cycle before claim/provider I/O. This enforcement is covered by unit tests and exact-head CI; new execution paths must integrate the same loader.

## Continuous improvement operating charter

The [Continuous Improvement and Research Charter](continuous-improvement-charter.md) is an additive layer over the established architecture and policy; it does not authorize a rewrite or override safety gates. Use the [Reusable Task-Completion Handoff Prompt](task-completion-handoff-prompt.md) or its [Arabic version](task-completion-handoff-prompt.ar.md) to resume work from the latest verified repository state after each cycle. Existing architecture, zero-spend policy, dry-run defaults, provider quotas, and approval gates remain authoritative.

## Current operating mode

Core v1 is merged. The system has a local SQLite task store, structured memory, deterministic workflow planner, provider discovery, quota-aware routing primitives, a provider-neutral model gateway, and a CLI.

The default posture remains conservative:

- Worker execution is dry-run oriented; do not treat it as an always-on production worker.
- Paid model calls and external writes are disabled by default.
- Provider catalog discovery is read-only and requires a configured provider credential.
- Live inference adapters are opt-in code paths; do not enable them until provider eligibility, quota limits, and credentials are explicitly configured and verified.
- Safe task types are now assigned to a named specialist role and required capability through a deterministic contract. This remains a dry-run assignment: it does not mean a persistent multi-agent team or model inference is running.

## Requirements and checks

Python 3.11 or later is required. Runtime dependencies are kept minimal.

```bash
python -m engine.cli audit
python -m unittest discover -s tests -v
```

CI runs the policy/agent-roster audit and unit tests on pushes to `main` and on pull requests targeting `main`. A workflow file is not proof of a successful run; verify the Actions result for the exact commit SHA.

## Task operations

```bash
python -m engine.cli enqueue --type research_brief --title "Example" --payload '{"topic":"agent frameworks"}'
python -m engine.cli list
python -m engine.cli run-once
python -m engine.cli run-batch --max-tasks 5 --max-seconds 60
python -m engine.cli audit-log
```

The bounded batch runner admits at most five tasks per invocation and uses the policy's `max_parallel_agents` setting (currently 2). `--max-parallel` may lower, but never exceed, this policy cap. Each worker independently claims a fenced task lease; the shared quota ledger continues to reserve provider capacity atomically. The time limit controls admission of new tasks, not forced interruption of work already running. The SQLite store supports task lifecycle operations, idempotency, bounded retries, and stale-task recovery. The current worker is a bounded invocation, not a persistent service. The optional remote D1 task adapter is selected only when both `RAAWAA_ENGINE_API_URL` and `RAAWAA_ENGINE_API_TOKEN` are configured; otherwise SQLite remains the default. The remote adapter covers enqueue, list, claim, heartbeat, finish, retry, and stale-lease recovery. Memory and model-usage ledgers remain local SQLite. D1 leases are capped at 15 minutes. This adapter does not make task execution persistent or enable external actions; those still require handler-level idempotency and explicit policy gates. Separately, the Cloudflare Worker is configured with a 15-minute Cron Trigger that only performs bounded stale-lease recovery. The merged Worker code now records a `worker_cron_tick` event in the existing D1 `audit_log`, including its cron expression and recovery counts, and the authenticated mobile status endpoint exposes the latest recorded tick. **This Worker code was deployed to production on 2026-11-03 at 00:45 UTC** from main SHA `9363d0eddd51d829bd405cb90dc39c81e001d3c9` (Worker version `ded17c7d-0201-431c-af7f-ad16e1f7f408`). The first post-deployment heartbeat was independently verified by authenticated mobile-status read in Task Runner run [#41556158915](https://github.com/ghith93/raawaa-autonomous-engine/actions/runs/41556158915) at 00:54:39 UTC: last seen `2026-11-03T00:45:04.494Z`, cron `*/15 * * * *`, stale leases requeued `0`, failed `0`. This is a Worker scheduler heartbeat, not proof that the Python Task Runner ran or processed tasks; the GitHub Actions Python consumer remains a separate bounded scheduler. The Task Runner's authenticated mobile-status probe now records the heartbeat timestamp, cron expression, and stale-lease recovery counts in its sanitized issue status. The latest confirmed schedule-triggered Task Runner run remains #41532657423 at 21:55 UTC on 2026-11-02. The latest Task Runner run, #41556158915 attempt 2 at 01:06 UTC on 2026-11-03, was a manual re-run of a push-triggered workflow, not a `schedule` event. It read D1 status with HTTP 200, confirmed zero pending/running tasks, and processed zero tasks (`queue_idle`). No successful schedule-triggered Task Runner run after the cron-phase resynchronization has yet been verified. No `schedule` event was present in the subsequent Actions history. PR #283 shifted the five-minute cron phase from minute 3 to minute 4 on main commit `68882524c0808f513dce31bc78ebd6fc9e0746c4` to refresh GitHub's schedule registration. This is a recovery attempt, not proof that scheduled execution has resumed; require a new successful `schedule`-triggered Task Runner event before declaring it healthy. GitHub notes that scheduled runs are best-effort and may be delayed or dropped, and that a stopped schedule may need its cron definition changed to restart ([workflow troubleshooting](https://docs.github.com/en/actions/how-tos/troubleshoot-workflows)).

## Task Runner schedule watchdog

PR #291 adds an optional fallback scheduler to the existing Cloudflare Worker. The current GitHub Actions Task Runner schedule remains primary. A second Cloudflare Cron runs at minutes 1, 6, 11, 16, 21, 26, 31, 36, 41, 46, 51, and 56 UTC, two minutes after the primary Task Runner schedule. It checks the fixed Task Runner workflow on main and skips if any run is queued/running, if any Task Runner run on main was created within the previous 10 minutes, or if a recent durable D1 dispatch intent exists. Otherwise it requests workflow_dispatch for task-runner.yml on main. A D1 intent is reserved atomically before the external dispatch so an ambiguous dispatch/audit failure cannot immediately cause duplicate requests.

The fallback uses a GitHub App installation token scoped to repository ID 1441352567 and Actions: write. It requires the explicit Cloudflare Worker variable `GITHUB_APP_WATCHDOG_ENABLED=true` plus Worker secrets `GITHUB_APP_ID`, `GITHUB_APP_INSTALLATION_ID`, and `GITHUB_APP_PRIVATE_KEY`. Without the exact opt-in value, the handler returns disabled before network or D1 access, even if App credentials exist. It does not use a PAT, add dependencies, or apply a D1 migration. The private key must be added directly in Cloudflare and never pasted into ChatGPT or committed to source. Full setup and verification steps are in the [GitHub App watchdog guide](operations/github-app-task-runner-watchdog.md).

PR #291 was merged and the Worker was deployed successfully on 2026-11-03 at 01:25 UTC from main SHA `4800d2aa3fb9dcd31c7dd0aaffe2e4d1b1b5638e` (Worker version `546a233f-5d93-4328-b71f-f3afed990b4c`). The public `/health` route returned `{"ok":true}`. The watchdog is now present in production code, but no watchdog-triggered `workflow_dispatch` run has yet been observed. The three Cloudflare Worker secret values are not readable through the available GitHub connector, so their configuration state cannot be independently confirmed here. Treat automatic dispatch as unverified until a real `workflow_dispatch` run is observed and its bounded batch result is checked. The existing D1 task ledger, concurrency group, safe_dry_run policy, disabled live inference, and disabled external writes remain unchanged.

## Memory operations

```bash
python -m engine.cli memory-add --category decision --content "Example decision" --source "operator"
python -m engine.cli memory-search --query "Example"
python -m engine.cli memory-list
python -m engine.cli memory-archive --id MEMORY_ID
```

Memory is local SQLite storage in the current implementation. Do not store API keys, passwords, sensitive customer data, or private order details in long-term memory. Back up and protect the database if it contains operational decisions.

## Model and provider operations

Before adding or enabling a provider profile, use the current [free-tier provider eligibility review](provider-free-tier-eligibility.md). It distinguishes public free-tier claims from account-specific limits and records data-handling and model-term restrictions.

```bash
python -m engine.cli model-status
python -m engine.cli provider-profile-status
python -m engine.cli quota-status
python -m engine.cli free-models --provider all
```

The provider-profile-status command reads the provider registry locally and reports account tier, model stage, production-use eligibility, quota caps, and eligibility blockers. The quota-status command reads the SQLite ledger and reports minute/day/month usage, eligible routes, and warnings at 80% or critical alerts at 100%; it performs no provider calls. Preview models remain blocked from the live gateway unless production use is explicitly permitted by the verified model terms. It never reads API key values or enables network execution. Provider catalog discovery is a deliberate, read-only network command. It requires the relevant provider credential and lists model identifiers only; it does not generate text or establish that a model is free. Treat unknown pricing, quota, regional availability, and commercial-use eligibility as unavailable.

The `UsageLedger` and active `QuotaAwareLiveGateway` now use atomic pre-call monetary reservations, integer nano-USD accounting, idempotent actual-cost settlement, and conservative retention of ambiguous reservations across daily/monthly windows. The live gateway reserves estimated spend before provider I/O, releases only reservations for explicit HTTP 429 or other pre-call rejection, records actual cost before rejecting an over-budget response, and blocks subsequent requests after an unexpected charge when the configured budget is zero. The current runtime still accepts verified zero-cost profiles exclusively and requires daily, monthly, and per-task budgets to be numeric zero with paid calls disabled. Boolean, missing, malformed, or non-zero budget values fail closed. No non-zero-cost route is eligible. `python -m engine.cli monetary-status` reports actual spend, reserved spend, configured hard caps, and unresolved reservation metadata without making network calls. Ambiguous reservations must not be released until provider-side evidence establishes whether the request was billed.

Per-task, daily, and monthly atomic spend caps are now integrated and tested; the configured per-task, daily, and monthly budgets remain zero, and only verified zero-cost profiles are eligible. Before any future paid route, verify provider-specific credit/billing settlement semantics, add reconciliation for ambiguous provider-side billing, and obtain explicit operator authorization for the policy change.

The model gateway and quota controls provide foundations for opt-in inference. Keep live inference disabled until:
1. the provider's current free-tier and commercial-use terms are verified;
2. token and request limits are configured conservatively;
3. credential handling and response validation tests pass;
4. quota reservation and settlement behavior is verified;
5. a clear operator kill switch and audit trail are available.

Never add paid fallback or automatic billing upgrades without explicit approval.


### Explicit live-inference opt-in path

The CLI and scheduled Task Runner now use the same local live-runtime factory. It constructs OpenAI-compatible adapters only when all of these independent gates pass:

1. The operator environment switch RAAWAA_ENGINE_LIVE_INFERENCE is exactly enabled.
2. config/policy.json uses live_inference_opt_in while paid calls, daily/monthly budgets, and external writes remain disabled/zero.
3. At least one provider profile is explicitly enabled in config/provider_profiles.json and passes the static zero-cost, free-tier, commercial-use, production-stage, and quota checks.
4. The profile's API-key environment variable is present. The factory checks presence only and never logs or prints the key.

The scheduled GitHub Actions Task Runner reads the optional repository Actions variable RAAWAA_ENGINE_LIVE_INFERENCE; it is unset by default. The variable alone cannot activate a route: policy mode and a profile must also be explicitly changed. Adapter construction is local only; the first provider request occurs only when the worker calls gateway.generate for a task. Before that call, the worker now requires an explicit public/internal data classification, rejects sensitive field names and common secret patterns, and enforces a 7,000-byte prompt-input ceiling. Unclassified, confidential, personal/customer, payment, credential, and proprietary-formula content is blocked before provider I/O.

Current configuration deliberately keeps policy mode at safe_dry_run and all profiles disabled. GPT-OSS 20B and 120B are production-stage candidates but remain disabled. Qwen 3.8-27B is Preview/evaluation-only and is rejected by the production eligibility gate. Do not change these defaults without the required explicit operator decision. No live inference request has been sent.

## GitHub Actions and deployment

GitHub Actions runs CI and is configured to invoke a scheduled, bounded D1 queue consumer every 5 minutes. Each invocation runs at most five tasks or admits new tasks for at most 60 seconds, whichever comes first; GitHub schedule delivery can be delayed and is not a real-time SLA. The Cloudflare Worker separately recovers expired leases every 15 minutes. Recent verification confirms the public Worker health GET and authenticated POST preflight both return HTTP 200. A one-time idempotent `system_audit` smoke task was enqueued and successfully processed through D1 claim, specialist dry-run, and lease-fenced finish. Neither scheduler is an always-on process.

Before selecting a host, verify free-tier limits, sleep behavior, persistent disk availability, outbound network rules, and whether the service can run without a payment method. Do not assume a free trial is a recurring free tier.


### D1 migration inventory (read-only first)

Use the manual [D1 Migration Inventory workflow](../.github/workflows/d1-migration-inventory.yml) to inspect the remote production migration ledger without applying changes. Follow the [D1 migration runbook](d1-migration-runbook.md) and record the exact run URL and checked-out SHA before considering a production migration. The separate `d1-migrate.yml` workflow applies **all** pending migrations after its typed confirmation; never dispatch it as a status check. Worker deployment and `/health` success do not establish D1 schema readiness. The latest recorded production application of migrations `0004` and `0005` is documented in the runbook; repeat read-only inventory before any subsequent migration. The Task Runner's sanitized D1 status comment also reports whether the Telegram receipt schema (`0005`) is available; this is schema-only telemetry and does not activate the webhook.

### Production deployment and D1 migration gates

Production side effects are deliberately separated from code merge:

- `.github/workflows/deploy-worker.yml` is manual-only. Dispatch requires the exact confirmation string `DEPLOY_CONTROL_WORKER`; a push or PR merge does not deploy the Worker.
- The automatic production migration workflow was removed. The retained `.github/workflows/d1-migrate.yml` is manual-only and requires the exact database confirmation `raawaa-autonomous-e`.
- A merge that changes `worker/**` or `migrations/**` therefore does not deploy code or apply schema changes by itself.
- Do not dispatch either workflow as part of routine CI or PR completion. First review the exact main SHA, migration status, rollback/recovery plan, and non-production integration evidence; production deployment/migration requires a separate explicit operator decision.

This gate exists because automatic migration on every push to main could have applied the new paper-ledger schema to the production D1 database immediately after merge, before non-production concurrency/restart validation.

## Secrets and permissions

Never commit API keys, access tokens, passwords, customer data, or private order data. Configure secrets only through repository Settings → Secrets and variables → Actions, and only for a reviewed integration that needs them. Prefer short-lived, narrowly scoped credentials. Do not paste secrets into chat, issues, logs, or memory.

Agents may research and propose changes. Policy gates must control execution. Keep external writes, deployment, financial actions, and consequential customer-facing operations behind explicit approval.

## Change process

- Work on an isolated feature branch and open a pull request.
- Add regression tests for behavior and policy changes.
- Mock provider calls in tests; do not use real paid inference in CI.
- Verify the CI result against the exact PR head SHA.
- Review permission, budget, integration, and deployment changes carefully.
- Never bypass a failing test or a policy gate to force a merge.

## Known limitations and next steps

- No persistent scheduler or always-on service; GitHub Actions and the Cloudflare cron are bounded scheduled jobs.
- No unattended live-agent loop: the scheduled worker defaults to dry-run. The Groq API key is configured and its read-only model-catalog check succeeded. GPT-OSS 20B/120B production candidates and Qwen 3.8-27B preview profile are staged in the registry, but every route remains disabled and no inference request has been made.
- No proactive alerting channel. A read-only Telegram command webhook (`/help`, `/status`, `/report`) is implemented behind operator-managed secrets and a private-chat allowlist; it is not automatically activated, and it does not provide scheduled alerts.
- The Cloudflare control Worker and D1 binding are deployed. The last observed Scheduled D1 Task Runner run (#22, 2026-11-02 22:46:36 UTC) passed the public health GET and authenticated POST preflight, but failed at the read-only `/v1/mobile/status` probe with HTTP 404; the enqueue and batch steps were skipped. Worker deployment run [#10](https://github.com/ghith93/raawaa-autonomous-engine/actions/runs/41546403591) later deployed commit `43b4f0d7ad2e3e63c10dd6f92fbebc00739610e7`. Subsequent unauthenticated route checks returned HTTP 200 for `/health` and `/mobile`, and HTTP 401 for `/v1/mobile/status`, confirming that the protected route now exists but not proving an authenticated D1 read. A post-deployment rerun of Task Runner run #22 (attempt 2) completed successfully: health GET and authenticated POST returned HTTP 200; the authenticated D1 mobile-status probe returned HTTP 200 with the paper-event and provider-usage ledgers available, zero pending/running tasks, and zero paper events. The idempotent smoke enqueue returned HTTP 200 with `created=false` and the existing task already `succeeded`; the bounded batch completed with `tasks_processed=0` and `stop_reason=queue_idle`. This verifies authenticated D1 status and an idle queue, not processing a new task in this attempt. The last observed schedule-triggered run remains #20 at 21:55 UTC; no later cron-triggered run has been verified, so periodic scheduling remains unproven. Live inference remains disabled.
- Lease-token fencing and periodic heartbeat renewal are implemented and covered by tests. External side effects still require handler-specific idempotency before activation.
- Local SQLite memory and model-usage state need an explicit backup/retention strategy for production.
- Groq Free model profiles are staged only: GPT-OSS 20B is preferred, GPT-OSS 120B is the fallback, and Qwen 3.8-27B is preview-only. Check `python -m engine.cli provider-profile-status`; this command is local/read-only. Rolling-minute, daily, monthly, and per-task quota controls are implemented, but live inference remains off pending explicit operator opt-in and data-classification controls.
- The former `workers.dev` HTTP 403 was Cloudflare Error 1010 / Browser Integrity Check: Cloudflare rejects Python's default `urllib` User-Agent on `workers.dev`. The remote task client and both Task Runner probes now send a descriptive `RaawaaAutonomousEngine/1.0` User-Agent. Do not disable Browser Integrity Check, Access, or WAF to solve this class of issue. The separate Access inventory API error says Access is not enabled; it was not the cause of the Worker 403.

Recommended next work: wire the disabled production GPT-OSS 20B/120B profiles through the existing quota gateway only after the operator explicitly authorizes live inference and data-classification controls are in place; implement monetary accounting before any non-zero-cost route; then continue with side-effect idempotency and a genuinely free persistent host assessment.

## Diagnosing the authenticated D1 task API

The scheduled task runner performs a read-free authenticated POST preflight to `/v1/diagnostics/auth` before it calls any task-management endpoint. A successful preflight returns HTTP 200 with an authentication confirmation and does not read or write D1. It exists to separate bearer-token/edge failures from failures in an individual task route.

Interpret the preflight result conservatively:

- **200:** the POST reached the Worker and the bearer token was accepted. If a later task endpoint fails, investigate that endpoint's route, method, handler, and D1 binding rather than changing the shared authentication policy.
- **401:** the request reached the Worker authentication guard, but the supplied token was not accepted. Verify that the GitHub Actions secret and deployed Worker secret were synchronized by the reviewed deployment workflow. Never print or paste either secret.
- **403:** inspect the safe `CF-Ray` and `cf-mitigated` response headers, when present, to determine whether Cloudflare edge controls intervened. Do not infer that every 403 is a WAF event, and do not disable Access, WAF, or authentication as a workaround.
- **5xx or transport failure:** investigate Worker execution, availability, and D1 binding health. Do not retry task mutations blindly unless their idempotency contract makes that safe.

The endpoint accepts POST only; GET returning 405 is expected and is not evidence that authenticated POST is broken. An unauthenticated task-list GET returning 401 is also expected. Neither result substitutes for the scheduled runner's authenticated POST test.

The preflight does not prove that `/v1/tasks/recover`, claim, heartbeat, or finish works. A successful end-to-end verification requires a subsequent scheduled Task Runner execution to pass the preflight and complete the bounded task batch. Check the exact Actions run and job logs; do not claim the 403 is resolved from a successful Worker deployment or CI run alone.


## Quota ledger and live-adapter safety

The optional quota-aware live gateway is disabled by default. Live calls require both the programmatic network flag and the operator environment kill switch `RAAWAA_ENGINE_LIVE_INFERENCE=enabled`; an unset or different value blocks provider I/O. The quota ledger records metadata-only audit events (reservation, completion, provider error, cooldown, quarantine, and kill-switch block) and filters out prompt/output fields. When explicitly enabled in a controlled test or approved deployment, it selects only a route with an HTTPS endpoint, separate HTTPS pricing/quota/terms evidence URLs (valid public hostnames, no embedded credentials, and no reserved placeholder domains such as `.example`, `.invalid`, `.test`, or localhost), a timezone-aware verification timestamp no more than 30 days old, verified free-tier and commercial-use eligibility, rolling-minute, daily, monthly, and per-task request/token caps, and a maximum output-token ceiling. Missing, malformed, future-dated, stale, or unknown profile data is not eligible. The gateway enforces the output ceiling before reserving quota or contacting an adapter. No real provider profile is currently configured by this repository. URL validation checks syntax and excludes reserved placeholders; it does not fetch or independently verify the source content, so an operator must verify each source before setting the profile's verification flag and timestamp.

Before provider I/O, every generation request must carry its task ID. The gateway atomically reserves one request and a conservative token estimate in SQLite using an immediate write transaction against per-task, daily, and monthly request/token caps. Per-task usage aggregates across provider routes, so rotating providers cannot reset a task's allowance. Concurrent reservations therefore compete against the same persisted caps. On a valid response, the reservation is settled to actual token usage. Settlement is idempotent only when a repeated call reports the same actual token count; a conflicting second settlement fails closed. Existing quota databases are migrated additively.

For timeouts, transport failures, malformed responses, 5xx, and other non-429 provider errors, retain the full estimate and stop route rotation because the provider may already have processed the request. Only an explicit HTTP 429 response applies a route cooldown and permits trying another verified route. Do not release an ambiguous reservation or replay the same request after an uncertain outcome.

The live gateway rejects mocked completions, empty text, mismatched provider/model identities, invalid token counts, and non-finite or negative costs. A verified-free route that reports a non-zero cost is quarantined; the gateway does not return the completion or rotate to another provider. The adapter's placeholder `cost_usd=0.0` is not evidence of free usage. Verify account-specific provider terms and pricing independently before enabling any live route.

The quota ledger enforces rolling 60-second request/token limits atomically alongside daily, monthly, and per-task request/token limits. Every provider profile must declare positive integer minute limits; missing or malformed values make the route ineligible. Minute reservations are included in the same SQLite immediate transaction as the other quota windows, so concurrent workers cannot each consume the last request/token allowance. Monthly limits are reserved atomically with daily limits before provider I/O. Do not assume the optional `monthly_credit_usd` profile field is an active monthly monetary cap; monetary-credit accounting is not implemented. Paid model calls remain disabled by default, and there is no automatic paid fallback.

## Quota-gated worker inference

The worker accepts an optional, programmatically constructed `QuotaAwareLiveGateway`. The default CLI and scheduled Task Runner do not construct or inject one, so the normal execution path remains a deterministic zero-network dry run. Live execution is considered only when the operator switch `RAAWAA_ENGINE_LIVE_INFERENCE=enabled` is present **and** a gateway has been explicitly injected. The gateway independently requires its programmatic network flag, a task ID, and at least one currently verified eligible provider profile with atomic per-task/rolling-minute/daily/monthly request and token reservations.

The worker sends the task title and JSON payload to the selected provider only in that explicitly enabled path. Treat queued payloads as untrusted input and do not enqueue secrets, passwords, private customer records, or other sensitive data. The live request is bounded to 256 output tokens. Provider/gateway failures are terminally blocked for that attempt rather than automatically retried, because a timeout or transport failure may mean the provider already processed the request. Lease heartbeat renewal and ownership fencing are enforced before result persistence. No provider profile or credential is shipped/configured by this change; therefore this wiring alone does not enable live inference or change scheduled jobs from dry-run mode.
