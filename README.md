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

**It is not yet a continuously running autonomous service.** A Cloudflare control Worker with a D1 binding has been deployed, and GitHub Actions is configured to invoke a bounded task batch every 15 minutes. However, recent Task Runner attempts are being stopped by HTTP 403 at the authenticated POST preflight, before task operations; successful end-to-end task processing is not yet verified. The normal worker path remains a deterministic dry run. There is no always-on scheduler, notification channel, or unattended external-action execution. Live inference is disabled by default, and no verified provider profile or credentials are configured.

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

1. Resolve the Cloudflare/edge HTTP 403 affecting the authenticated Task Runner preflight; compare the public `/health` GET with the authenticated POST diagnostic now recorded by the workflow.
2. Verify a complete bounded D1 batch on a current `main` commit; do not equate a successful CI run or Worker deployment with task processing.
3. Add operation-specific idempotency contracts before enabling side-effecting handlers or external writes.
4. Verify a genuinely free host with durable storage before moving beyond scheduled bounded jobs to a continuously running service.
5. Configure a live provider only after account-specific free-tier, commercial-use, pricing, endpoint, and quota evidence is verified; preserve the kill switch and quota ledger. Add notifications and external integrations incrementally.

See [Architecture](docs/architecture.md) and [Operations](docs/operations.md).
