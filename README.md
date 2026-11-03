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
- An isolated Cloudflare Workflows durability prototype with deterministic local tests; it is not deployed or connected to production D1.

**It is not yet a continuously running autonomous service.** The deployed Cloudflare control Worker is bound to production D1. Production D1 migrations `0001`–`0005` are recorded as applied. The latest migration run applied `0004` and `0005` on reviewed main SHA `e5eb80c395155151417ad82b761e0b7d5829ce6a`; the preceding read-only inventory showed `0001`–`0003` already applied. Dedicated non-production D1 `raawaa-engine-staging` is remotely reachable and migrations `0001`–`0005` have been applied successfully. The initial apply is recorded in [Staging migration run #41572155554](https://github.com/ghith93/raawaa-autonomous-engine/actions/runs/41572155554); the CI-gated `staging-migrate` job subsequently verified the target separation and `No migrations to apply` in [main CI run #41572391383](https://github.com/ghith93/raawaa-autonomous-engine/actions/runs/41572391383). The Staging Worker itself has not been deployed, and production D1/Worker were not changed. The D1 monetary-ledger schema/API/adapter and local integration tests are now implemented in source, but the CI-gated Staging Worker deployment and remote concurrent-client/Worker-redeploy verification are now defined but still require a successful post-merge main run; provider-side hard-stop review remains outstanding. Live inference remains disabled pending those gates.

**Task execution and scheduling evidence**
- GitHub Actions Task Runner is configured for bounded batches every five minutes (maximum five tasks / 60 seconds per invocation). The GitHub `schedule` trigger remains the primary cadence; GitHub scheduled events are best-effort, so the Cloudflare watchdog is an independent fallback rather than a guarantee of uninterrupted execution.
- **Watchdog activation and end-to-end dispatch verified on 2026-11-03:** Task Runner run [#41569188010](https://github.com/ghith93/raawaa-autonomous-engine/actions/runs/41569188010) was created at 02:56:42 UTC on `main`, event `workflow_dispatch`, actor `raawaa-engine[bot]`, and completed successfully at 02:56:58 UTC. Its authenticated D1 status read returned HTTP 200 with `watchdog_ready=ready`, `watchdog_missing=none`, and matching latest dispatch request / D1 intent timestamps `2026-11-03T02:56:40.404Z`. The bounded batch completed with `tasks_processed=0` and `queue_idle`; this verifies configuration, dispatch, authenticated D1 access, and an idle bounded run, **not** execution of a real business task.
- The last Worker stale-lease Cron heartbeat visible in that run was `2026-11-03T02:45:34.076Z` for `*/15 * * * *`, with `0` leases requeued and `0` failures. Continue monitoring later Cron heartbeats and the next primary `schedule`-triggered Task Runner run independently; a successful watchdog dispatch does not prove that GitHub's primary schedule has resumed reliably.
- The public Worker health route and authenticated mobile-status route both returned HTTP 200 in run #41556158915. The unauthenticated mobile-status route remains protected by authentication; no token value is exposed in logs.
- PR #310 adds an authenticated, read-only Task Runner watchdog panel to `/mobile` and `GET /v1/mobile/status`. It reports only whether the three GitHub App secrets and explicit opt-in variable are present, the names of missing settings, and sanitized dispatch-intent/request metadata from D1. It never returns secret values. On 2026-11-03, readiness was observed as `ready` and a real App-authored `workflow_dispatch` run (#41569188010) completed successfully; the run found an idle queue (`tasks_processed=0`, `queue_idle`). This proves dispatch and bounded-run health, not real task completion.
- PR #291 adds an optional Cloudflare Cron watchdog as a fallback for the missing GitHub schedule events. It runs two minutes after the expected five-minute GitHub Task Runner tick, skips if any Task Runner run on `main` was created in the last 10 minutes or a durable D1 dispatch-intent lock exists, and otherwise dispatches only `task-runner.yml` on `main` using a one-repository GitHub App token with `Actions: write`. The watchdog was first deployed on 2026-11-03 at 01:25 UTC from main SHA `4800d2aa3fb9dcd31c7dd0aaffe2e4d1b1b5638e` (Worker version `546a233f-5d93-4328-b71f-f3afed990b4c`). After PR #305 added an explicit opt-in gate, the Worker was redeployed at 01:34 UTC from main SHA `15e0ff5c4051cd396a59e76dd9503db5448ff52a` (Worker version `b1cb1429-3914-412c-b681-20c245466269`); deployment run [#41560533199](https://github.com/ghith93/raawaa-autonomous-engine/actions/runs/41560533199) succeeded and the public `/health` route again returned `{"ok":true}`. After PR #310 added the authenticated readiness panel, the Worker was redeployed at 01:42 UTC from main SHA `e791ee478577f0307006cda2deb264dc65c324fc` (Worker version `c6854d5d-88fd-4922-95fc-f832f338d2cc`); deployment run [#41561443015](https://github.com/ghith93/raawaa-autonomous-engine/actions/runs/41561443015) succeeded. The public `/health` route returned `{"ok":true}`, `/mobile` displayed the Task Runner watchdog panel, and unauthenticated `/v1/mobile/status` returned HTTP 401. The explicit `GITHUB_APP_WATCHDOG_ENABLED=enabled` switch must be added manually as a Cloudflare Worker variable only after verifying the GitHub App installation and all three secrets; it is deliberately not committed in `wrangler.toml`. Missing App secrets or a missing opt-in switch cause a no-op. The watchdog has now successfully dispatched Task Runner run [#41569188010](https://github.com/ghith93/raawaa-autonomous-engine/actions/runs/41569188010) after the operator configured the three Cloudflare App secrets and the explicit opt-in variable. The authenticated D1 probe returned `ready`; the bounded batch completed idle with zero tasks. Secret values remain intentionally unreadable to the GitHub connector. See [GitHub App watchdog setup](docs/operations/github-app-task-runner-watchdog.md).

The previous `workers.dev` HTTP 403 was traced to Cloudflare Error 1010 (Browser Integrity Check) rejecting Python's default `urllib` User-Agent; Worker clients now send a descriptive application User-Agent. The normal task handler remains a deterministic dry run. There is no persistent always-on Python agent worker, notification channel, or unattended external-action execution.

Groq Free account access and model-catalog visibility were verified through a read-only authenticated catalog check. GPT-OSS 20B/120B are staged production-model candidates and Qwen 3.8-27B is preview-only; all provider routes remain disabled. No inference request has been sent. The Groq credential is used for catalog verification only; live inference is disabled by default, and non-zero-cost routes remain blocked pending monetary accounting and explicit approval.

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

1. Implement the durable D1 monetary reservation/settlement ledger in issue #332 and verify provider-specific credit settlement semantics. Existing D1 quota controls meter provider requests/tokens; they are not a durable USD spend ledger. Keep cloud live inference blocked until monetary reservations, ambiguous-outcome reconciliation, concurrency and restart tests pass.
2. Complete provider-specific data-classification and retention review for each intended workload; keep confidential formulae, credentials, and customer/order data out of unsuitable provider tiers.
3. Add operation-specific idempotency contracts and explicit approval gates before enabling side-effecting handlers or external writes.
4. Verify remote access to `raawaa-engine-staging`, inspect its migration state, then validate the D1 provider quota ledger and the new monetary ledger there with concurrency, restart recovery, ambiguous provider outcomes, and settlement idempotency. Production D1 migrations `0001`–`0005` are already applied; never apply staging migrations to production. Memory and model-usage ledgers remain local SQLite.
5. Evaluate a genuinely durable, zero-cost execution path without treating scheduled GitHub Actions as a guaranteed always-on service.
6. Add notifications and external integrations incrementally while preserving the kill switch, quota ledger, and default dry-run posture.

All agents must follow [AGENTS.md](AGENTS.md) and the permanent [Master Autonomous Execution Directive](docs/master-autonomous-execution-directive.ar.md). A user message containing only `.` resumes execution from the latest verified state. Runtime enforcement by every deployed agent remains an engineering requirement until implemented and tested. See [Architecture](docs/architecture.md) and [Operations](docs/operations.md). The additive [Continuous Improvement and Research Charter](docs/continuous-improvement-charter.md) governs proactive research and incremental capability development without replacing existing architecture or safety policy. The [Reusable Task-Completion Handoff Prompt](docs/task-completion-handoff-prompt.md) can be sent after each work cycle to resume from the latest verified state. For the current free-tier, commercial-use, and data-handling assessment, see [Provider free-tier eligibility](docs/provider-free-tier-eligibility.md).
