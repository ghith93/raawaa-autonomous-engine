# Operations and contributor guide

## Permanent autonomous operating directive

The [Master Autonomous Execution Directive](master-autonomous-execution-directive.ar.md) is permanent repository policy. Every agent and cycle must read it with the [Continuous Improvement Charter](continuous-improvement-charter.md), [Architecture](architecture.md), and applicable policies. A user message consisting only of `.` means resume from the latest verified repository state, execute the highest-priority safe work, and continue. See [AGENTS.md](../AGENTS.md) for startup instructions. Runtime prompt-loader enforcement remains an engineering requirement until implemented and tested.

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

CI runs the policy/agent-roster audit and unit tests on pushes to `main`, `bootstrap/**`, `feature/**`, and `fix/**`, and on pull requests targeting `main`. A workflow file is not proof of a successful run; verify the Actions result for the exact commit SHA.

## Task operations

```bash
python -m engine.cli enqueue --type research_brief --title "Example" --payload '{"topic":"agent frameworks"}'
python -m engine.cli list
python -m engine.cli run-once
python -m engine.cli audit-log
```

The SQLite store supports task lifecycle operations, idempotency, bounded retries, and stale-task recovery. The current worker is a bounded invocation, not a persistent service. The optional remote D1 task adapter is selected only when both `RAAWAA_ENGINE_API_URL` and `RAAWAA_ENGINE_API_TOKEN` are configured; otherwise SQLite remains the default. The remote adapter covers enqueue, list, claim, heartbeat, finish, retry, and stale-lease recovery. Memory and model-usage ledgers remain local SQLite. D1 leases are capped at 15 minutes. This adapter does not make task execution persistent or enable external actions; those still require handler-level idempotency and explicit policy gates.

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

GitHub Actions runs CI and is configured to invoke a scheduled, bounded D1 queue consumer every 15 minutes. Each invocation runs at most five tasks or admits new tasks for at most 60 seconds, whichever comes first; GitHub schedule delivery can be delayed and is not a real-time SLA. The Cloudflare Worker separately recovers expired leases every 15 minutes. Recent verification confirms the public Worker health GET and authenticated POST preflight both return HTTP 200. A one-time idempotent `system_audit` smoke task was enqueued and successfully processed through D1 claim, specialist dry-run, and lease-fenced finish. Neither scheduler is an always-on process.

Before selecting a host, verify free-tier limits, sleep behavior, persistent disk availability, outbound network rules, and whether the service can run without a payment method. Do not assume a free trial is a recurring free tier.

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
- No notification channel.
- The Cloudflare control Worker and D1 binding are deployed. The latest Task Runner passed health GET and authenticated POST preflights (HTTP 200) and completed a one-time D1 smoke task successfully in dry-run mode. Live inference remains disabled.
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
