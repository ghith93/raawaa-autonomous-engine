# Operations and contributor guide

## Latest verified operational state — 2026-11-03 16:56 UTC

This section is the current snapshot. Older run IDs and migration notes in the historical sections below document earlier milestones and may be superseded by this snapshot.

- **Main and CI:** the latest code-bearing main commit `ecc9b848a3133a26dc2da873552e6c9b545647cc` passed the full post-merge pipeline in [run #41678035968](https://github.com/ghith93/raawaa-autonomous-engine/actions/runs/41678035968). A later documentation-only README commit `5bf6db34797bb137d849d560b6bfce5451b5e143` is on main; its CI run [#41679208202](https://github.com/ghith93/raawaa-autonomous-engine/actions/runs/41679208202) was still running when this snapshot was written.
- **Staging database and Worker:** isolated Staging D1 migrations `0001`–`0007` were applied and verified. The Staging Worker was deployed and the remote verification passed token-quota concurrency/settlement, task-invocation identity and lease fencing, retry/ambiguous-outcome controls, daily/monthly/per-task monetary caps, operator-policy tamper rejection, idempotency, quarantine, Worker-redeploy persistence, and authenticated aggregate-only status. The same CI run completed the authenticated mobile-status readback successfully. Ephemeral Staging bearer and quota-admin tokens were revoked after verification.
- **Production boundary:** this cycle made no production Worker deployment and no production D1 migration or write. Production remains separate from Staging.
- **Research telemetry:** scheduled discovery run [#41678035935](https://github.com/ghith93/raawaa-autonomous-engine/actions/runs/41678035935) completed successfully. It wrote one scan event and two source observations to the pinned Staging D1 and verified all three records on readback.
- **Task Runner:** bounded run [#41678478461](https://github.com/ghith93/raawaa-autonomous-engine/actions/runs/41678478461) completed successfully with authenticated Worker access and healthy status probes, but processed zero tasks (`queue_idle`). This is not evidence of a real business task being completed or of a persistent always-on agent.
- **Merged changes:** quota cooldown monotonicity fix #395, research-scan/source-freshness telemetry #396, Staging-credential wiring fix #397, and this operations-state documentation update are merged. PR #378 was superseded by #396 and closed without merge.
- **Provider gate:** PR #380 remains a draft and is behind main. Its OpenRouter profiles are disabled and non-commercial; no inference route or paid fallback was activated. A bounded public-prompt smoke request previously reached the Nemotron free endpoint but failed strict response/model validation; no second request was sent.
- **Issues and repository protection:** issue #332 is closed after the durable monetary ledger implementation and isolated Staging verification. Issue #318 is closed as completed by #379; the active `main-protection` ruleset (ID `26464558`) requires PRs, exact `test` CI success, up-to-date branches and resolved review threads, and blocks deletion/force-push without bypass actors.
- **Execution gates:** live model inference, paid fallback, external writes, and customer-facing actions remain disabled. The six-agent roster is declarative; it is not six running agents. GitHub Actions and the Worker watchdog are bounded schedulers, not a guaranteed continuously running Python agent service.

## Permanent autonomous operating directive

The [Master Autonomous Execution Directive](master-autonomous-execution-directive.ar.md) is permanent repository policy. Every agent and cycle must read it with the [Continuous Improvement Charter](continuous-improvement-charter.md), [Architecture](architecture.md), and applicable policies. A user message consisting only of `.` means resume from the latest verified repository state, execute the highest-priority safe work, and continue. See [AGENTS.md](../AGENTS.md) for startup instructions. The Python worker now enforces policy loading through `engine.operating_policy.load_operating_policy()` before every task claim. It reads the complete required documents, validates them, and supplies a compact trusted policy summary with SHA-256 fingerprints before task payload in the live model prompt. Missing or invalid policy blocks the cycle before claim/provider I/O. This enforcement is covered by unit tests and exact-head CI; new execution paths must integrate the same loader.

## Continuous improvement operating charter

The [Continuous Improvement and Research Charter](continuous-improvement-charter.md) is an additive layer over the established architecture and policy; it does not authorize a rewrite or override safety gates. Use the [Reusable Task-Completion Handoff Prompt](task-completion-handoff-prompt.md) or its [Arabic version](task-completion-handoff-prompt.ar.md) to resume work from the latest verified repository state after each cycle. Existing architecture, the operator-approved USD 15 all-in monthly hard cap, dry-run defaults, provider quotas, and approval gates remain authoritative.

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

The bounded batch runner admits at most five tasks per invocation and uses the policy's `max_parallel_agents` setting (currently 2). `--max-parallel` may lower, but never exceed, this policy cap. Each worker independently claims a fenced task lease; the shared quota ledger continues to reserve provider capacity atomically. The time limit controls admission of new tasks, not forced interruption of work already running. The SQLite store supports task lifecycle operations, idempotency, bounded retries, and stale-task recovery. The current worker is a bounded invocation, not a persistent service. The optional remote D1 task adapter is selected only when both `RAAWAA_ENGINE_API_URL` and `RAAWAA_ENGINE_API_TOKEN` are configured; otherwise SQLite remains the default. The remote adapter covers enqueue, list, claim, heartbeat, finish, retry, and stale-lease recovery. Memory and the local CLI ledger remain SQLite. Cloud execution now has a D1-backed monetary adapter in PR #341; the cloud runtime never falls back to ephemeral SQLite when the control Worker is configured. Dedicated Staging D1 `raawaa-engine-staging` exists and migrations `0001`–`0005` are applied there; migration `0006` is intentionally pending PR review and the CI-gated Staging verification. Production D1 remains unchanged. D1 leases are capped at 15 minutes. This adapter does not make task execution persistent or enable external actions; those still require handler-level idempotency and explicit policy gates. Separately, the Cloudflare Worker is configured with a 15-minute Cron Trigger that only performs bounded stale-lease recovery. The merged Worker code now records a `worker_cron_tick` event in the existing D1 `audit_log`, including its cron expression and recovery counts, and the authenticated mobile status endpoint exposes the latest recorded tick. **This Worker code was deployed to production on 2026-11-03 at 00:45 UTC** from main SHA `9363d0eddd51d829bd405cb90dc39c81e001d3c9` (Worker version `ded17c7d-0201-431c-af7f-ad16e1f7f408`). The first post-deployment heartbeat was independently verified by authenticated mobile-status read in Task Runner run [#41556158915](https://github.com/ghith93/raawaa-autonomous-engine/actions/runs/41556158915) at 00:54:39 UTC: last seen `2026-11-03T00:45:04.494Z`, cron `*/15 * * * *`, stale leases requeued `0`, failed `0`. This is a Worker scheduler heartbeat, not proof that the Python Task Runner ran or processed tasks; the GitHub Actions Python consumer remains a separate bounded scheduler. The Task Runner's authenticated mobile-status probe now records the heartbeat timestamp, cron expression, and stale-lease recovery counts in its sanitized issue status. The latest confirmed schedule-triggered Task Runner run remains #41532657423 at 21:55 UTC on 2026-11-02. The latest confirmed watchdog dispatch, [#41569188010](https://github.com/ghith93/raawaa-autonomous-engine/actions/runs/41569188010), was created at 02:56:42 UTC on 2026-11-03 with event `workflow_dispatch` and actor `raawaa-engine[bot]`. It completed successfully at 02:56:58 UTC. The authenticated D1 status read returned HTTP 200 with `watchdog_ready=ready` and `watchdog_missing=none`; dispatch request and D1 intent timestamps matched at `2026-11-03T02:56:40.404Z`. The bounded batch processed zero tasks (`queue_idle`), so this verifies watchdog configuration, dispatch, authenticated D1 access, and a successful idle bounded run—not processing of a real task. The primary GitHub `schedule` event remains a separate reliability check; a successful watchdog fallback does not prove the primary schedule has resumed.

GitHub notes that scheduled runs are best-effort and may be delayed or dropped, and that a stopped schedule may need its cron definition changed to restart ([workflow troubleshooting](https://docs.github.com/en/actions/how-tos/troubleshoot-workflows)).

## Task Runner schedule watchdog

PR #291 adds an optional fallback scheduler to the existing Cloudflare Worker. The current GitHub Actions Task Runner schedule remains primary. A second Cloudflare Cron runs at minutes 1, 6, 11, 16, 21, 26, 31, 36, 41, 46, 51, and 56 UTC, two minutes after the primary Task Runner schedule. It checks the fixed Task Runner workflow on main and skips if any run is queued/running, if any Task Runner run on main was created within the previous 10 minutes, or if a recent durable D1 dispatch intent exists. Otherwise it requests workflow_dispatch for task-runner.yml on main. A D1 intent is reserved atomically before the external dispatch so an ambiguous dispatch/audit failure cannot immediately cause duplicate requests.

The fallback uses a GitHub App installation token scoped to repository ID 1441352567 and Actions: write. It requires Cloudflare Worker secrets GITHUB_APP_ID, GITHUB_APP_INSTALLATION_ID, and GITHUB_APP_PRIVATE_KEY plus the explicit Worker variable GITHUB_APP_WATCHDOG_ENABLED=enabled. If any secret or the opt-in variable is absent, the handler returns disabled without network or D1 access. It does not use a PAT, add dependencies, or apply a D1 migration. The private key must be added directly in Cloudflare and never pasted into ChatGPT or committed to source. Full setup and verification steps are in the [GitHub App watchdog guide](operations/github-app-task-runner-watchdog.md).

PR #291's watchdog implementation was first deployed at 01:25 UTC from main SHA `4800d2aa3fb9dcd31c7dd0aaffe2e4d1b1b5638e` (Worker version `546a233f-5d93-4328-b71f-f3afed990b4c`). After PR #305 added a second fail-closed opt-in gate, the Worker was redeployed at 01:34 UTC from main SHA `15e0ff5c4051cd396a59e76dd9503db5448ff52a` (Worker version `b1cb1429-3914-412c-b681-20c245466269`); deployment run [#41560533199](https://github.com/ghith93/raawaa-autonomous-engine/actions/runs/41560533199) succeeded and the public `/health` route again returned `{"ok":true}`. **Watchdog dispatch is verified:** run [#41569188010](https://github.com/ghith93/raawaa-autonomous-engine/actions/runs/41569188010) was authored by `raawaa-engine[bot]`, used `workflow_dispatch`, reported `watchdog_ready=ready` and `watchdog_missing=none`, and completed successfully with `queue_idle`. This proves the App-authenticated dispatch path and bounded runner execution, but it did not process a task. The private key remains stored only in Cloudflare; its value is not retrievable through the GitHub connector. Continue monitoring the next primary schedule event and later watchdog heartbeats. The safe_dry_run policy, disabled live inference, and disabled external writes remain unchanged.

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

The local `UsageLedger` and active `QuotaAwareLiveGateway` use atomic pre-call monetary reservations, integer nano-USD accounting, idempotent actual-cost settlement, and conservative retention of ambiguous reservations across daily/monthly windows. The new `D1MonetaryLedger` adapter is selected when the cloud control Worker URL/token are configured; cloud GitHub Actions execution fails closed rather than falling back to ephemeral SQLite. D1 reservations move through `reserved → in_flight → settled|ambiguous`; release is permitted only while still `reserved`, before provider I/O. An explicit HTTP 429 is settled at zero rather than released because provider I/O has already begun. Actual cost is recorded before an over-budget response is rejected, and the provider/model route is quarantined if actual cost exceeds its estimate or the configured cap. The repository now enforces a USD 15 all-in monthly envelope: USD 12 model spend (USD 0.40/day and USD 0.20/task), USD 0 infrastructure paid-overage allowance, and USD 3 unallocated reserve. Verified-free profiles remain the only eligible routes today; paid fallback remains disabled. Missing, malformed, unknown, or inconsistent budget values fail closed. `python -m engine.cli monetary-status` reports actual spend, reserved spend, configured hard caps, and unresolved reservation metadata without making network calls. Ambiguous reservations must not be released until provider-side evidence establishes whether the request was billed.

Per-task, daily, and monthly atomic spend caps are integrated and tested; the active caps are USD 0.20/task, USD 0.40/day, and USD 12/month. Only verified zero-cost profiles are eligible today. Before any paid route, verify provider-specific spend-block semantics, set a provider-side hard stop no greater than the remaining model allocation, add reconciliation for ambiguous billing, and update the policy only after explicit operator approval.

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
2. config/policy.json uses live_inference_opt_in, paid calls and external writes remain disabled, and the validated USD 12 monthly / USD 0.40 daily / USD 0.20 per-task model caps remain in force.
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


## Operator-approved permanent budget policy (2026-11-03)

The project-wide spending ceiling is **USD 15 per billing month**, inclusive of model inference and infrastructure. The current enforceable allocation is deliberately more conservative:

| Envelope | Hard cap | Execution rule |
|---|---:|---|
| All-in project ceiling | $15.00/month | Never raise automatically |
| Model inference | $12.00/month | Atomic pre-call reservation; actual + pending reservations count |
| Model inference daily | $0.40/day | Stops new paid reservations for the UTC day |
| Model inference per task | $0.20/task | Aggregates across providers/routes |
| Cloudflare/GitHub Actions paid overages | $0.00 | Free/included allowances only |
| Unallocated reserve | $3.00/month | Not spendable; requires a separate explicit reallocation |

**Hybrid routing:** use verified-free, production-eligible providers first. The paid fallback is intentionally configured but disabled. It may only be enabled after the provider account exposes a verified provider-enforced spend block or prepaid balance no greater than the remaining USD 12 model allocation. A dashboard alert, estimated-cost check, or delayed billing report is not a hard stop. Never auto-upgrade a provider plan.

**Provider selection evidence (reviewed 2026-11-03):**
- **Groq Free — first candidate:** the current account/model/quota profile was operator-confirmed. Groq documents Free-tier over-limit behavior as rate-limit errors rather than charges. The D1 monetary ledger is implemented in PR #341, but remote Staging verification and provider hard-stop evidence remain gates; keep the configured route disabled until both pass and live inference is separately approved. [Groq rate limits](https://console.groq.com/docs/rate-limits) · [Groq billing FAQ](https://console.groq.com/docs/billing-faqs).
- **Groq Developer — possible paid fallback, not activated:** Groq documents organization-level spend limits that block API access at the cap. This is a better hard-stop candidate than a warning-only budget, but it requires a paid-tier account and owner-level billing configuration. Do not upgrade the account automatically; if later approved, set its organization cap no higher than the remaining USD 12 model allocation and verify the block behavior first. [Groq spend limits](https://console.groq.com/docs/spend-limits).
- **Cloudflare Workers AI — conditional free candidate:** Cloudflare documents 10,000 Neurons/day at no charge on Workers Free; exceeding the free allocation fails rather than billing. Workers Paid charges USD 0.011 per 1,000 Neurons above the daily free allocation. The account's actual plan is not yet verified, so do not activate Workers AI or assume it is free. [Workers AI pricing](https://developers.cloudflare.com/workers-ai/platform/pricing/).
- **Gemini API — free tier only for now:** Google's project spend-cap feature is experimental and may allow around 10 minutes of overage while billing data catches up. It is therefore not the preferred paid hard-stop fallback under a strict no-overrun requirement. [Gemini billing and spend caps](https://ai.google.dev/gemini-api/docs/billing).



**Cloudflare billing is a separate external control.** Cloudflare budget alerts are informational and do not stop usage. The account-level Spend Policies API documents a `usage_block` action for Workers and Durable Objects, but it is product-wide for the selected Cloudflare product and could disable unrelated Workers. The read-only account inventory on 2026-11-03 returned HTTP 200 with zero active/paid subscriptions; the Spend Policies endpoint returned HTTP 404, so no product-level `usage_block` policy is configured or verified. Cloudflare Workers Free and D1 Free also publish hard usage ceilings that reject requests/queries after the included allowance; these are an infrastructure-level fallback only, not a model-provider spend cap. The project is configured `free_only`, paid Cloudflare overages are disabled, and the Worker uses a `workers.dev` hostname. Do not treat Cloudflare's Free quotas as evidence that Groq/Gemini/other model-provider billing is hard-capped. The policy `model_policy.provider_hard_stop_verified` therefore remains `false`; live inference is blocked until provider-specific hard-stop evidence is reviewed. References: [Workers pricing and Free limits](https://developers.cloudflare.com/workers/platform/pricing/), [D1 pricing and Free limits](https://developers.cloudflare.com/d1/platform/pricing/), [Spend Policies API](https://developers.cloudflare.com/api/resources/billing/subresources/spend_policies/).

**Current activation state:** the D1 monetary-ledger schema, authenticated Worker API, Python adapter, route quarantine, local integration tests, and CI-gated Staging Worker verification harness are implemented on PR #341. The PR is not yet merged; migration `0006` and the Staging Worker deployment are therefore still pending. Production D1 and the production Worker have not been changed. Live inference remains disabled, all provider profiles remain disabled, paid fallback remains disabled, and `model_policy.provider_hard_stop_verified=false` is an explicit runtime gate. The local CLI may use SQLite; cloud GitHub Actions execution requires the shared D1 adapter and fails closed if the Worker URL/token or endpoint is unavailable. After merge, CI must apply migration `0006` to Staging and pass remote concurrency, crash/restart, idempotency, ambiguity, and over-cap quarantine tests. Provider-side hard-stop evidence must be independently reviewed before any live-inference opt-in.

**Operational commands:**
```bash
python -m engine.cli audit
python -m engine.cli monetary-status
python -m engine.cli provider-profile-status
```

These commands are read-only. Monitor actual provider invoices and Cloudflare/GitHub billing dashboards; unresolved provider reservations must continue consuming budget until reconciled with provider-side evidence.


### Mandatory blocker before any cloud inference or paid model routing

The cloud monetary adapter and D1 Worker API are implemented in PR #341, while local CLI development retains SQLite. The shared D1 ledger is not yet production-active: migration `0006` is pending Staging verification, and no production migration or deployment is authorized by this PR. Cloud execution must use the D1 monetary and token quota ledgers; it must never fall back to ephemeral SQLite.

The USD 12/month model cap, USD 0.40/day cap, and USD 0.20/task cap are enforced by the D1 monetary policy. Live inference additionally requires `model_policy.provider_hard_stop_verified=true` with auditable provider, scope, limit, verification date, and HTTPS source evidence. That flag is currently `false`, so the live runtime factory fails closed even if a key and operator switch are present. Paid fallback remains disabled. Do not enable either until the provider-side hard stop is independently verified and the Staging CI gate passes.


## Quota administration credential

The control Worker separates ordinary quota reservation/settlement access (`ENGINE_API_TOKEN`) from administrative quota writes (`ENGINE_QUOTA_ADMIN_TOKEN`). The bounded Python Task Runner passes the corresponding repository secret as `RAAWAA_ENGINE_QUOTA_ADMIN_TOKEN`; the live runtime forwards it only to the D1 quota client. Keep the two token values distinct. If the admin token is absent, quota audit writes and cooldown/record operations fail closed; do not work around this by reusing the ordinary API token. No production token is created or rotated by code changes. Configure the same high-entropy admin token in the Cloudflare control Worker secret and the GitHub Actions repository secret only when an approved operational need exists. Live inference remains disabled until all existing provider, budget, and operator gates are independently satisfied.
