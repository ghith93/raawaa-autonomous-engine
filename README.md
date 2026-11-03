# raawaa-autonomous-engine

An independent, cost-controlled AI operating-system foundation. This repository is separate from all Raawaa storefront repositories.

## Current implementation status

**Core v1 is merged to `main`.** The repository now contains:

- A dependency-free Python core, conservative action policy, and specialist-agent roster with deterministic task-to-agent assignment contracts.
- A SQLite task store with idempotency, task lifecycle management, bounded retries, and stale-task recovery.
- Structured local memory with search and archive operations.
- Deterministic workflow planning and validation.
- Provider catalog discovery (read-only) and quota-aware routing controls.
- Provider-neutral model gateway interfaces, validated response accounting, and opt-in live inference adapters.
- GitHub Actions CI and maintenance workflows.
- Durable Cloudflare D1 token-quota, monetary-reservation/settlement, and per-task model-invocation ledgers, with local regression tests and an isolated remote Staging verification harness.
- A deployed Staging-only Cloudflare Worker and D1 environment, isolated from production, with authenticated mobile-status readback and CI-gated verification.
- Explicit scheduled research-scan and per-source freshness telemetry for public GitHub and Crossref discovery.

**It is not yet a continuously running autonomous service.** The production control Worker remains bound to production D1; this verification cycle did not deploy to or mutate production. Dedicated Staging D1 `raawaa-engine-staging` has migrations `0001`–`0007` applied, and the isolated Staging Worker was deployed and remotely verified from main commit `ecc9b848a3133a26dc2da873552e6c9b545647cc` in [main CI run #41678035968](https://github.com/ghith93/raawaa-autonomous-engine/actions/runs/41678035968). The remote verification passed token-quota concurrency and settlement, durable multi-step invocation identities and lease fencing, explicit 429 route rotation, blocked ambiguous/settled replay, daily/monthly/per-task monetary caps, operator-policy tamper rejection, idempotency, pre-I/O release, ambiguous settlement, over-estimate quarantine, Worker redeploy persistence, and aggregate-only authenticated mobile status. Both ephemeral Staging bearer and quota-admin tokens were revoked after verification. The CI-gated authenticated mobile-status readback also completed successfully in that run, confirming the current CI and default kill-switch snapshots through the Staging API. Production D1/Worker were not changed. Live inference remains disabled; provider-side hard-stop and workload-specific data/terms reviews remain outstanding.

**Task execution and scheduling evidence**
- GitHub Actions Task Runner is configured for bounded batches every five minutes (maximum five tasks / 60 seconds per invocation). The GitHub `schedule` trigger remains the primary cadence; GitHub scheduled events are best-effort, so the Cloudflare watchdog is an independent fallback rather than a guarantee of uninterrupted execution.
- **Watchdog activation and end-to-end dispatch verified on 2026-11-03:** Task Runner run [#41569188010](https://github.com/ghith93/raawaa-autonomous-engine/actions/runs/41569188010) was created at 02:56:42 UTC on `main`, event `workflow_dispatch`, actor `raawaa-engine[bot]`, and completed successfully at 02:56:58 UTC. Its authenticated D1 status read returned HTTP 200 with `watchdog_ready=ready`, `watchdog_missing=none`, and matching latest dispatch request / D1 intent timestamps `2026-11-03T02:56:40.404Z`. The bounded batch completed with `tasks_processed=0` and `queue_idle`; this verifies configuration, dispatch, authenticated D1 access, and an idle bounded run, **not** execution of a real business task.
- The last Worker stale-lease Cron heartbeat visible in that run was `2026-11-03T02:45:34.076Z` for `*/15 * * * *`, with `0` leases requeued and `0` failures. Continue monitoring later Cron heartbeats and the next primary `schedule`-triggered Task Runner run independently; a successful watchdog dispatch does not prove that GitHub's primary schedule has resumed reliably.
- The public Worker health route and authenticated mobile-status route both returned HTTP 200 in run #41556158915. The unauthenticated mobile-status route remains protected by authentication; no token value is exposed in logs.
- PR #310 adds an authenticated, read-only Task Runner watchdog panel to `/mobile` and `GET /v1/mobile/status`. It reports only whether the three GitHub App secrets and explicit opt-in variable are present, the names of missing settings, and sanitized dispatch-intent/request metadata from D1. It never returns secret values. On 2026-11-03, readiness was observed as `ready` and a real App-authored `workflow_dispatch` run (#41569188010) completed successfully; the run found an idle queue (`tasks_processed=0`, `queue_idle`). This proves dispatch and bounded-run health, not real task completion.
- PR #291 adds an optional Cloudflare Cron watchdog as a fallback for the missing GitHub schedule events. It runs two minutes after the expected five-minute GitHub Task Runner tick, skips if any Task Runner run on `main` was created in the last 10 minutes or a durable D1 dispatch-intent lock exists, and otherwise dispatches only `task-runner.yml` on `main` using a one-repository GitHub App token with `Actions: write`. The watchdog was first deployed on 2026-11-03 at 01:25 UTC from main SHA `4800d2aa3fb9dcd31c7dd0aaffe2e4d1b1b5638e` (Worker version `546a233f-5d93-4328-b71f-f3afed990b4c`). After PR #305 added an explicit opt-in gate, the Worker was redeployed at 01:34 UTC from main SHA `15e0ff5c4051cd396a59e76dd9503db5448ff52a` (Worker version `b1cb1429-3914-412c-b681-20c245466269`); deployment run [#41560533199](https://github.com/ghith93/raawaa-autonomous-engine/actions/runs/41560533199) succeeded and the public `/health` route again returned `{"ok":true}`. After PR #310 added the authenticated readiness panel, the Worker was redeployed at 01:42 UTC from main SHA `e791ee478577f0307006cda2deb264dc65c324fc` (Worker version `c6854d5d-88fd-4922-95fc-f832f338d2cc`); deployment run [#41561443015](https://github.com/ghith93/raawaa-autonomous-engine/actions/runs/41561443015) succeeded. The public `/health` route returned `{"ok":true}`, `/mobile` displayed the Task Runner watchdog panel, and unauthenticated `/v1/mobile/status` returned HTTP 401. The explicit `GITHUB_APP_WATCHDOG_ENABLED=enabled` switch must be added manually as a Cloudflare Worker variable only after verifying the GitHub App installation and all three secrets; it is deliberately not committed in `wrangler.toml`. Missing App secrets or a missing opt-in switch cause a no-op. The watchdog has now successfully dispatched Task Runner run [#41569188010](https://github.com/ghith93/raawaa-autonomous-engine/actions/runs/41569188010) after the operator configured the three Cloudflare App secrets and the explicit opt-in variable. The authenticated D1 probe returned `ready`; the bounded batch completed idle with zero tasks. Secret values remain intentionally unreadable to the GitHub connector. See [GitHub App watchdog setup](docs/operations/github-app-task-runner-watchdog.md).

The previous `workers.dev` HTTP 403 was traced to Cloudflare Error 1010 (Browser Integrity Check) rejecting Python's default `urllib` User-Agent; Worker clients now send a descriptive application User-Agent. The normal task handler remains a deterministic dry run. There is no persistent always-on Python agent worker, notification channel, or unattended external-action execution.

Groq Free account access and model-catalog visibility were verified through a read-only authenticated catalog check. GPT-OSS 20B/120B remain staged candidates and Qwen 3.8-27B is preview-only; all Groq routes remain disabled. One bounded public-prompt request was sent to OpenRouter's pinned Nemotron 3 Ultra free endpoint on 2026-11-03; it reached the endpoint but failed strict response/model validation. No response body or secret was logged, no second request or paid fallback was attempted, and both OpenRouter profiles remain disabled and non-commercial. The endpoint explicitly warns against sending confidential or personal data and states that use is logged for security and NVIDIA product improvement. Nemotron's OpenMDW-1.1 model license permits commercial and non-commercial use, but that does not override the separate OpenRouter/NVIDIA hosted-service terms. Do not send Raawaa formulae, credentials, customer/order data, or other sensitive information to this free endpoint. Live inference remains disabled by default.

## Safety and cost model

- Hybrid/free-first: verified free providers are preferred; paid fallback remains disabled until provider-enforced spend blocking is verified.
- Hard budget: USD 15/month all-in; USD 12/month model cap, USD 0.40/day, USD 0.20/task; USD 0 paid infrastructure overages; USD 3 unallocated reserve.
- Unknown provider pricing, quota, or commercial eligibility is treated as unavailable until verified.
- Secrets never belong in source control; use narrowly scoped repository secrets only for deliberately enabled integrations.
- Agents propose work; policy gates determine what may execute.
- External writes, deployment, financial actions, and consequential customer-facing operations require explicit policy and approval.
- Retries are bounded. Lease-token fencing and periodic heartbeat renewal are implemented and tested; side-effecting handlers still need operation-specific idempotency guarantees before activation.
- GitHub Actions is suitable for CI and bounded jobs, not a substitute for a durable always-on service.
- No changes to `Raawaa-store` or `raawaa-official`.

## Quick start

Requires Python 3.11+.

```bash
python -m engine.cli audit
python -m unittest discover -s tests -v
python -m engine.cli enqueue --type research_brief --title "Example" --payload '{"topic":"agent frameworks"}'
python -m engine.cli list
python -m engine.cli run-once
python -m engine.cli monetary-status
```

## Repository map

- `engine/`: core runtime, task store, memory, orchestration, provider routing and CLI.
- `config/`: agent roster, execution policy, provider catalog, free-resource metadata, and execution-policy metadata.
- `docs/`: architecture, operating model, subsystem contracts, and staged roadmap.
- `.github/workflows/`: CI and bounded maintenance automation.
- `tests/`: unit and regression tests.

## Next engineering priorities

1. Confirm the final authenticated Staging mobile-status readback in [main CI run #41678035968](https://github.com/ghith93/raawaa-autonomous-engine/actions/runs/41678035968), including visibility of the monetary ledger and current kill-switch snapshot. Keep production unchanged.
2. Complete provider-specific license, hosted-service terms, data-retention, account-quota, and commercial-use review before enabling any model. Keep OpenRouter free routes disabled; its free Nemotron endpoint logs use for security/product improvement and prohibits confidential or personal inputs.
3. Build a genuinely durable, low-cost execution path. GitHub Actions schedules and the Cloudflare watchdog are bounded execution mechanisms, not proof of an always-on autonomous agent service.
4. Exercise real non-sensitive queued tasks end-to-end and verify task completion, memory updates, monitoring, and recovery. Current successful watchdog evidence includes idle batches and does not by itself prove real business-task completion.
5. Add operation-specific idempotency contracts and explicit approval gates before enabling side-effecting handlers, external writes, deployments, financial actions, or customer-facing operations.
6. Add notifications and external integrations incrementally while preserving the kill switch, quota ledger, monetary ledger, default dry-run posture, and strict separation between Staging and production. Production D1 migrations `0001`–`0005` are recorded as applied; Staging migrations `0001`–`0007` are verified separately.

All agents must follow [AGENTS.md](AGENTS.md) and the permanent [Master Autonomous Execution Directive](docs/master-autonomous-execution-directive.ar.md). A user message containing only `.` resumes execution from the latest verified state. Runtime enforcement by every deployed agent remains an engineering requirement until implemented and tested. See [Architecture](docs/architecture.md) and [Operations](docs/operations.md). The additive [Continuous Improvement and Research Charter](docs/continuous-improvement-charter.md) governs proactive research and incremental capability development without replacing existing architecture or safety policy. The [Reusable Task-Completion Handoff Prompt](docs/task-completion-handoff-prompt.md) can be sent after each work cycle to resume from the latest verified state. For the current free-tier, commercial-use, and data-handling assessment, see [Provider free-tier eligibility](docs/provider-free-tier-eligibility.md).

## Research and development

- [Continuous R&D operating system](docs/research-and-development-operating-system.ar.md) — roles, cadence, evidence lifecycle, prioritization, experiment gates, metrics, and current activation boundaries.
- [Capability Expansion Director prompt](docs/prompts/capability-expansion-director.ar.md) — reusable execution prompt for an AI coding/research operator.
