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

**It is not yet a continuously running autonomous service.** The deployed Cloudflare control Worker is bound to production D1. Production D1 migrations `0001`–`0005` are recorded as applied. The latest migration run applied `0004` and `0005` on reviewed main SHA `e5eb80c395155151417ad82b761e0b7d5829ce6a`; the preceding read-only inventory showed `0001`–`0003` already applied. No dedicated non-production D1 database is configured, so remote concurrency/restart verification remains outstanding; local D1/Workerd tests are not a substitute. Live inference remains disabled.

**Task execution and scheduling evidence**
- GitHub Actions Task Runner is configured for a bounded batch every five minutes. PR #283 shifted the cron phase from minute 3 to minute 4 on main commit `68882524c0808f513dce31bc78ebd6fc9e0746c4` to refresh schedule registration after a gap in scheduled events. The latest confirmed `schedule`-triggered Task Runner run remains [#41532657423](https://github.com/ghith93/raawaa-autonomous-engine/actions/runs/41532657423) at 21:55 UTC on 2026-11-02; the first post-resync scheduled Task Runner run is still pending verification.
- The latest Task Runner invocation, [#41562069768](https://github.com/ghith93/raawaa-autonomous-engine/actions/runs/41562069768), was triggered by `push` at 01:48 UTC on 2026-11-03 after the status-probe update. It passed health/authenticated preflights and the authenticated D1 mobile-status read (`HTTP 200`); the bounded batch processed zero tasks and stopped at `queue_idle`. D1 reported `tasks_pending=0` and `tasks_running=0`. The sanitized status comment reported watchdog `not_ready`, missing `GITHUB_APP_ID`, `GITHUB_APP_INSTALLATION_ID`, `GITHUB_APP_PRIVATE_KEY`, and `GITHUB_APP_WATCHDOG_ENABLED`, with no dispatch request or intent recorded. This push-triggered run does not prove five-minute schedule reliability; the latest confirmed `schedule`-triggered Task Runner run remains [#41532657423](https://github.com/ghith93/raawaa-autonomous-engine/actions/runs/41532657423) at 21:55 UTC on 2026-11-02.
- The separate Cloudflare Worker Cron runs every 15 minutes for bounded stale-lease recovery. Its latest `worker_cron_tick` D1 heartbeat was verified by Task Runner run [#41562069768](https://github.com/ghith93/raawaa-autonomous-engine/actions/runs/41562069768) at 01:48 UTC on 2026-11-03: last seen `2026-11-03T01:45:34.300Z`, cron `*/15 * * * *`, stale leases requeued `0`, failed `0`. This confirms Worker Cron execution only; the same run was push-triggered and does not prove the separate GitHub Actions Task Runner schedule.
- The public Worker health route and authenticated mobile-status route both returned HTTP 200 in run #41556158915. The unauthenticated mobile-status route remains protected by authentication; no token value is exposed in logs.
- PR #310 adds an authenticated, read-only Task Runner watchdog panel to `/mobile` and `GET /v1/mobile/status`. It reports only whether the three GitHub App secrets and explicit opt-in variable are present, the names of missing settings, and sanitized dispatch-intent/request metadata from D1. It never returns secret values. `ready=true` means configuration is present, not that GitHub App permissions or dispatch have been proven; only a real `workflow_dispatch` Task Runner run and completed batch establish end-to-end operation.
- PR #291 adds an optional Cloudflare Cron watchdog as a fallback for the missing GitHub schedule events. It runs two minutes after the expected five-minute GitHub Task Runner tick, skips if any Task Runner run on `main` was created in the last 10 minutes or a durable D1 dispatch-intent lock exists, and otherwise dispatches only `task-runner.yml` on `main` using a one-repository GitHub App token with `Actions: write`. The watchdog was first deployed on 2026-11-03 at 01:25 UTC from main SHA `4800d2aa3fb9dcd31c7dd0aaffe2e4d1b1b5638e` (Worker version `546a233f-5d93-4328-b71f-f3afed990b4c`). After PR #305 added an explicit opt-in gate, the Worker was redeployed at 01:34 UTC from main SHA `15e0ff5c4051cd396a59e76dd9503db5448ff52a` (Worker version `b1cb1429-3914-412c-b681-20c245466269`); deployment run [#41560533199](https://github.com/ghith93/raawaa-autonomous-engine/actions/runs/41560533199) succeeded and the public `/health` route again returned `{"ok":true}`. After PR #310 added the authenticated readiness panel, the Worker was redeployed at 01:42 UTC from main SHA `e791ee478577f0307006cda2deb264dc65c324fc` (Worker version `c6854d5d-88fd-4922-95fc-f832f338d2cc`); deployment run [#41561443015](https://github.com/ghith93/raawaa-autonomous-engine/actions/runs/41561443015) succeeded. The public `/health` route returned `{"ok":true}`, `/mobile` displayed the Task Runner watchdog panel, and unauthenticated `/v1/mobile/status` returned HTTP 401. The explicit `GITHUB_APP_WATCHDOG_ENABLED=enabled` switch must be added manually as a Cloudflare Worker variable only after verifying the GitHub App installation and all three secrets; it is deliberately not committed in `wrangler.toml`. Missing App secrets or a missing opt-in switch cause a no-op. No watchdog-triggered `workflow_dispatch` run has yet been observed, and the GitHub connector cannot inspect Cloudflare secret values; activation therefore remains unverified. See [GitHub App watchdog setup](docs/operations/github-app-task-runner-watchdog.md).

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

1. Verify provider-specific credit settlement semantics and add reconciliation for ambiguous billing outcomes; daily/monthly/per-task atomic spend caps are integrated, but the USD 15 all-in monthly cap is configured, the model allocation is capped at USD 12, paid fallback remains disabled, and non-zero-cost routes remain ineligible.
2. Complete provider-specific data-classification and retention review for each intended workload; keep confidential formulae, credentials, and customer/order data out of unsuitable provider tiers.
3. Add operation-specific idempotency contracts and explicit approval gates before enabling side-effecting handlers or external writes.
4. Validate the D1 provider quota ledger against a dedicated non-production D1 database, including applying the migration sequence there, concurrency, restart recovery, and settlement idempotency. Production D1 migrations `0001`–`0005` are already applied; do not enable live inference until the non-production verification gate passes. Memory and model-usage ledgers remain local SQLite.
5. Evaluate a genuinely durable, zero-cost execution path without treating scheduled GitHub Actions as a guaranteed always-on service.
6. Add notifications and external integrations incrementally while preserving the kill switch, quota ledger, and default dry-run posture.

All agents must follow [AGENTS.md](AGENTS.md) and the permanent [Master Autonomous Execution Directive](docs/master-autonomous-execution-directive.ar.md). A user message containing only `.` resumes execution from the latest verified state. Runtime enforcement by every deployed agent remains an engineering requirement until implemented and tested. See [Architecture](docs/architecture.md) and [Operations](docs/operations.md). The additive [Continuous Improvement and Research Charter](docs/continuous-improvement-charter.md) governs proactive research and incremental capability development without replacing existing architecture or safety policy. The [Reusable Task-Completion Handoff Prompt](docs/task-completion-handoff-prompt.md) can be sent after each work cycle to resume from the latest verified state. For the current free-tier, commercial-use, and data-handling assessment, see [Provider free-tier eligibility](docs/provider-free-tier-eligibility.md).
