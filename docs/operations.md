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

The CI workflow runs on pushes to main and bootstrap/**, feature/**, and fix/** branches, pull requests targeting main, and manual dispatch. It validates the policy and runs unit tests. It does not access provider secrets or deploy anything.

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


## CLI commands

The implemented local commands are:

```bash
python -m engine.cli enqueue --type research_brief --title "Example" --payload '{"topic":"agent frameworks"}'
python -m engine.cli list
python -m engine.cli run-once
python -m engine.cli audit-log
python -m engine.cli memory-add --category decision --content "Example decision" --source "operator"
python -m engine.cli memory-search --query "Example"
python -m engine.cli memory-list
python -m engine.cli memory-archive --id MEMORY_ID
python -m engine.cli model-status
```

Provider catalog discovery is a deliberate, read-only network command and requires a configured provider key:

```bash
python -m engine.cli free-models --provider all
```

This command lists provider model identifiers only. It does not generate text or establish that a model is free. Do not store secrets, credentials, customer records, or private order details in long-term memory.
