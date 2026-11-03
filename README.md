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
- The latest Task Runner invocation, [#41556158915](https://github.com/ghith93/raawaa-autonomous-engine/actions/runs/41556158915), was triggered by `push` at 00:54 UTC on 2026-11-03. It passed health/authenticated preflights and the authenticated D1 mobile-status read (`HTTP 200`); its bounded batch processed zero tasks and stopped at `queue_idle`. The idempotent smoke task already existed in `succeeded` state. This push-triggered run does not prove five-minute schedule reliability.
- The separate Cloudflare Worker Cron runs every 15 minutes for bounded stale-lease recovery. Its `worker_cron_tick` D1 heartbeat was verified again by Task Runner run [#41556158915 attempt 2](https://github.com/ghith93/raawaa-autonomous-engine/actions/runs/41556158915) at 01:06:28 UTC on 2026-11-03: last seen `2026-11-03T01:00:04.433Z`, cron `*/15 * * * *`, stale leases requeued `0`, failed `0`. The same authenticated status read reported `HTTP 200`, `tasks_pending=0`, `tasks_running=0`; its bounded batch processed zero tasks and stopped at `queue_idle`. This was a manual re-run, not a scheduled Task Runner event.
- The public Worker health route and authenticated mobile-status route both returned HTTP 200 in run #41556158915. The unauthenticated mobile-status route remains protected by authentication; no token value is exposed in logs.
- PR #291 adds an optional Cloudflare Cron watchdog as a fallback for the missing GitHub schedule events. It checks every five minutes, skips if a Task Runner run is active or a scheduled run is newer than 10 minutes, and otherwise dispatches only `task-runner.yml` on `main` using a one-repository GitHub App token with `Actions: write`. It is fail-closed and remains dormant until `GITHUB_APP_ID`, `GITHUB_APP_INSTALLATION_ID`, and `GITHUB_APP_PRIVATE_KEY` are configured as Cloudflare Worker secrets. It is not deployed or activated by this PR. See [GitHub App watchdog setup](docs/operations/github-app-task-runner-watchdog.md).

The previous `workers.dev` HTTP 403 was traced to Cloudflare Error 1010 (Browser Integrity Check) rejecting Python's default `urllib` User-Agent; Worker clients now send a descriptive application User-Agent. The normal task handler remains a deterministic dry run. There is no persistent always-on Python agent worker, notification channel, or unattended external-action execution.

Groq Free account access and model-catalog visibility were verified through a read-only authenticated catalog check. GPT-OSS 20B/120B are staged production-model candidates and Qwen 3.8-27B is preview-only; all provider routes remain disabled. No inference request has been sent. The Groq credential is used for catalog verification only; live inference is disabled by default, and non-zero-cost routes remain blocked pending monetary accounting and explicit approval.

## Safety and cost model

- Free-first: paid API calls and infrastructure are disabled by default.
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

1. Verify provider-specific credit settlement semantics and add reconciliation for ambiguous billing outcomes; daily/monthly/per-task atomic spend caps are integrated, but all budgets remain zero and non-zero-cost routes remain ineligible.
2. Complete provider-specific data-classification and retention review for each intended workload; keep confidential formulae, credentials, and customer/order data out of unsuitable provider tiers.
3. Add operation-specific idempotency contracts and explicit approval gates before enabling side-effecting handlers or external writes.
4. Validate the D1 provider quota ledger against a dedicated non-production D1 database, including applying the migration sequence there, concurrency, restart recovery, and settlement idempotency. Production D1 migrations `0001`–`0005` are already applied; do not enable live inference until the non-production verification gate passes. Memory and model-usage ledgers remain local SQLite.
5. Evaluate a genuinely durable, zero-cost execution path without treating scheduled GitHub Actions as a guaranteed always-on service.
6. Add notifications and external integrations incrementally while preserving the kill switch, quota ledger, and default dry-run posture.

All agents must follow [AGENTS.md](AGENTS.md) and the permanent [Master Autonomous Execution Directive](docs/master-autonomous-execution-directive.ar.md). A user message containing only `.` resumes execution from the latest verified state. Runtime enforcement by every deployed agent remains an engineering requirement until implemented and tested. See [Architecture](docs/architecture.md) and [Operations](docs/operations.md). The additive [Continuous Improvement and Research Charter](docs/continuous-improvement-charter.md) governs proactive research and incremental capability development without replacing existing architecture or safety policy. The [Reusable Task-Completion Handoff Prompt](docs/task-completion-handoff-prompt.md) can be sent after each work cycle to resume from the latest verified state. For the current free-tier, commercial-use, and data-handling assessment, see [Provider free-tier eligibility](docs/provider-free-tier-eligibility.md).
