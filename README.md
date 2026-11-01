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

**It is not yet a continuously running autonomous service.** The worker remains dry-run oriented; there is no always-on scheduler, production deployment, notification channel, or unattended external-action execution. Live inference is not enabled by default. Provider discovery requires credentials and makes read-only catalog requests.

## Safety and cost model

- Free-first: paid API calls and infrastructure are disabled by default.
- Unknown provider pricing, quota, or commercial eligibility is treated as unavailable until verified.
- Secrets never belong in source control; use narrowly scoped repository secrets only for deliberately enabled integrations.
- Agents propose work; policy gates determine what may execute.
- External writes, deployment, financial actions, and consequential customer-facing operations require explicit policy and approval.
- Retries are bounded; side-effecting handlers must add ownership/heartbeat and idempotency guarantees before activation.
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

1. Add worker heartbeat and lease ownership tokens; prove safe recovery under concurrent workers.
2. Add idempotency contracts for side-effecting task handlers before enabling any external action.
3. Implement a bounded scheduler/worker deployment only after verifying a genuinely free host and persistent storage.
4. Add an agent execution loop with reviewer/policy gates, observability, and hard quota enforcement.
5. Add notifications and external integrations incrementally, with explicit permissions and tests.

See [Architecture](docs/architecture.md) and [Operations](docs/operations.md).
