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
- CLI commands for task operations, memory, model status, provider discovery, and audit logs.
- GitHub Actions CI and maintenance workflows.

**It is not yet a continuously running autonomous service.** A Cloudflare control Worker with a D1 binding is deployed, and GitHub Actions invokes a bounded task batch every 15 minutes. The previous `workers.dev` HTTP 403 was traced to Cloudflare Error 1010 (Browser Integrity Check) rejecting Python's default `urllib` User-Agent; all Worker clients now send a descriptive application User-Agent. The latest verified Task Runner passed both the public health GET and authenticated POST (HTTP 200), enqueued a one-time idempotent smoke task, and processed it successfully through the D1 claim, specialist dry-run, and lease-fenced finish path. The normal worker path remains a deterministic dry run. There is no always-on scheduler, notification channel, or unattended external-action execution. Live inference is disabled by default, and no verified provider profile or credentials are configured.

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
```

## Repository map

- `engine/`: core runtime, task store, memory, orchestration, provider routing and CLI.
- `config/`: agent roster, execution policy, provider catalog, and free-resource program metadata.
- `docs/`: architecture, operating model, subsystem contracts, and staged roadmap.
- `.github/workflows/`: CI and bounded maintenance automation.
- `tests/`: unit and regression tests.

## Next engineering priorities

1. Verify current account-specific free-provider terms, pricing, quotas, endpoints, and commercial-use eligibility before adding any provider profile or credential.
2. Implement and test monetary spend/credit accounting before enabling any non-zero-cost route; paid model calls remain disabled.
3. Add operation-specific idempotency contracts and explicit approval gates before enabling side-effecting handlers or external writes.
4. Verify a genuinely free host with durable storage before moving beyond scheduled bounded jobs to a continuously running service.
5. Add notifications and external integrations incrementally while preserving the kill switch, quota ledger, and default dry-run posture.

All agents must follow [AGENTS.md](AGENTS.md) and the permanent [Master Autonomous Execution Directive](docs/master-autonomous-execution-directive.ar.md). A user message containing only `.` resumes execution from the latest verified state. Runtime enforcement by every deployed agent remains an engineering requirement until implemented and tested. See [Architecture](docs/architecture.md) and [Operations](docs/operations.md). The additive [Continuous Improvement and Research Charter](docs/continuous-improvement-charter.md) governs proactive research and incremental capability development without replacing existing architecture or safety policy. The [Reusable Task-Completion Handoff Prompt](docs/task-completion-handoff-prompt.md) can be sent after each work cycle to resume from the latest verified state. For the current free-tier, commercial-use, and data-handling assessment, see [Provider free-tier eligibility](docs/provider-free-tier-eligibility.md).
