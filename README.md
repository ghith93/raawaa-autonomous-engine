# raawaa-autonomous-engine

An independent, cost-controlled AI operating system foundation. This repository is separate from all Raawaa storefront repositories.

## Current status

**Phase 1 — foundation in review.** The current implementation is a safe, dependency-free Python skeleton with configuration validation, explicit action gates, tests, and GitHub Actions CI. It does not yet call AI providers, execute external actions, or run a persistent agent service.

## Principles

- Free-first: no paid API calls or infrastructure by default.
- Secrets never belong in source control; use GitHub Actions secrets only when a provider integration is deliberately enabled.
- Least privilege, auditable actions, bounded retries, and explicit approval for consequential operations.
- Agents propose work; policy gates decide what may execute.
- GitHub Actions is suitable for CI and bounded scheduled jobs, not a substitute for a continuously running server or durable queue.
- No changes to `Raawaa-store` or `raawaa-official`.

## Quick start

Requires Python 3.11+.

```bash
python -m engine.cli audit
python -m unittest discover -s tests -v
```

## Repository map

- `engine/`: small, dependency-free core.
- `config/`: agent roster and conservative execution policy.
- `docs/`: architecture, operating model, and staged roadmap.
- `.github/workflows/ci.yml`: tests on pushes and pull requests.

## Next phases

1. Add durable task storage and a scheduler with idempotency.
2. Add provider adapters behind a model router, with quotas and hard spend limits.
3. Add specialist agents and reviewer workflows.
4. Add integrations only after permission and security review.
5. Deploy a persistent worker only after confirming a genuinely free hosting path and its limits.

See [Architecture](docs/architecture.md) and [Operations](docs/operations.md).
