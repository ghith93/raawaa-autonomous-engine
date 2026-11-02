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

**It is not yet a continuously running autonomous service.** A Cloudflare control Worker with a D1 binding is deployed, and GitHub Actions invokes a bounded task batch every 5 minutes. The D1 adapter supports the task lifecycle and now includes an additive durable provider quota ledger; memory and model-usage ledgers remain local SQLite. Production D1 migrations `0001` through `0005` are recorded as applied. The latest production migration run #2 applied `0004` and `0005` on reviewed main SHA `e5eb80c395155151417ad82b761e0b7d5829ce6a`; the preceding inventory showed `0001`–`0003` already applied. No dedicated non-production D1 database is configured, so remote non-production concurrency/restart verification remains outstanding; local D1/Workerd tests are not a substitute. Live inference remains disabled. The previous `workers.dev` HTTP 403 was traced to Cloudflare Error 1010 (Browser Integrity Check) rejecting Python's default `urllib` User-Agent; all Worker clients now send a descriptive application User-Agent. The last observed Scheduled D1 Task Runner run (#22, 2026-11-02 22:46:36 UTC) passed public health GET and authenticated POST preflights but failed at the read-only `/v1/mobile/status` probe with HTTP 404; enqueue and batch steps were skipped. Worker deployment run #10 later deployed commit `43b4f0d7ad2e3e63c10dd6f92fbebc00739610e7`. Subsequent unauthenticated checks returned HTTP 200 for `/health` and `/mobile`, and HTTP 401 for `/v1/mobile/status`, confirming route presence and authentication enforcement but not an authenticated D1 read. A post-deployment rerun of Task Runner run #22 (attempt 2) completed successfully: health GET and authenticated POST returned HTTP 200; the authenticated D1 mobile-status probe returned HTTP 200 with the paper-event and provider-usage ledgers available, zero pending/running tasks, and zero paper events. The idempotent smoke enqueue returned HTTP 200 with `created=false` and the existing task already `succeeded`; the bounded batch completed with `tasks_processed=0` and `stop_reason=queue_idle`. This verifies authenticated D1 status and an idle queue, not processing a new task in this attempt. The last observed schedule-triggered run remains #20 at 21:55 UTC; no later cron-triggered run has been verified, so periodic scheduling remains unproven. The normal worker path remains a deterministic dry run. There is no persistent always-on agent worker, notification channel, or unattended external-action execution.

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
