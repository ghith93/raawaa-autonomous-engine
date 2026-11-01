# Operations and contributor guide

## Current mode

The initial release is deliberately `safe_dry_run`: zero dollar budgets, paid model calls disabled, and external writes disabled. The agent roster is descriptive configuration only; no AI agents are invoked yet.

## Run checks locally

Python 3.11 or later is required. No third-party packages are needed.

```bash
python -m engine.cli audit
python -m unittest discover -s tests -v
```

## GitHub Actions

The CI workflow runs on pushes to the main/bootstrap/feature branches, pull requests targeting main, and manual dispatch. It validates the policy and runs unit tests. It does not access provider secrets or deploy anything.

## Secrets

Never commit API keys, access tokens, passwords, customer data, or private order data. Add secrets only through repository Settings → Secrets and variables → Actions, and only when a reviewed integration requires them. Prefer short-lived, narrowly scoped credentials.

## Change process

- Work on a feature branch.
- Add tests for policy and execution changes.
- Keep provider calls mocked in tests.
- Request review for changes to permissions, budgets, integrations, or deployment.
- Do not merge or deploy a change that bypasses approval gates.

## Current limitations

There is no persistent scheduler, task database, model router, live agent execution, notification channel, or production deployment in this phase. GitHub Actions CI is operational only after GitHub runs the workflow; the presence of a workflow file alone is not proof of a successful run.
