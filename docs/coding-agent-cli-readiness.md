# Coding-agent CLI readiness

## Scope

This repository uses GitHub Actions as a bounded, ephemeral Linux execution environment. This check installs and version-checks three candidate coding-agent CLIs without authenticating, invoking a model, reading repository secrets, modifying the repository workspace, or creating commits.

- OpenCode CLI: installed from the official npm package `@opencode/cli`.
- OpenAI Codex CLI: installed from the official npm package `@openai/codex`.
- Google Antigravity CLI: installed using Google's official Linux installer; binary expected at `~/.local/bin/agy`.

The workflow is `.github/workflows/coding-agent-cli-smoke.yml`. It runs on a pull request that changes this workflow or this document, and can also be manually dispatched after it is merged.

## What a passing run proves

A passing run proves only that the three command-line programs can be installed and report a version on the current GitHub-hosted Ubuntu runner. It does **not** prove account authentication, model access, free-tier eligibility, model quality, persistent credentials, autonomous task execution, or continuous availability.

## Current integration boundaries

- The existing Python `engine.agent_runtime` is a deterministic task-to-specialist contract and dry-run handler; it is not a coding-agent CLI adapter.
- The existing Cloudflare Worker + D1 system is the control plane and durable task ledger. GitHub Actions is an ephemeral bounded consumer, not an always-on Linux host.
- The current live inference path is separately gated by provider eligibility, provider hard-stop verification, durable D1 quota and monetary ledgers, data classification, and the operator kill switch. Installing a CLI must not bypass these gates.
- Do not add model credentials to the workflow, expose secret values, or make model calls as part of this installation check.
- Do not enable paid APIs, paid fallback, account upgrades, or infrastructure overages.

## Authentication is a separate gate

OpenCode, Codex CLI, and Antigravity CLI can be installed without credentials, but useful agent execution requires an eligible authenticated provider. Browser-based personal-account OAuth is not automatically available to an ephemeral GitHub Actions job. A persistent server would need a supported non-interactive authentication method and a narrowly scoped secret, or a supported device/remote OAuth handoff.

Before connecting any CLI to task execution, verify:
1. The exact account authentication method and current plan/quota.
2. That the selected model is free under that account, not merely that the CLI is open source.
3. That provider terms permit the intended repository and business use.
4. That provider-side limits prevent unexpected billing; otherwise keep the route disabled.
5. That the CLI runs in an isolated workspace with least-privilege GitHub permissions and produces a reviewable branch/PR.
6. That the existing operating-policy loader, D1 quota ledger, D1 monetary ledger, kill switch, data-classification rules, and task idempotency contracts are enforced before any model or external-tool call.

## Follow-up

After the installation smoke run passes, evaluate one CLI at a time in an isolated, non-production task. Prefer a supported personal-account OAuth flow where available; do not assume that a browser login from a local device can be copied to a hosted runner. Keep inference disabled until all gates above are verified.
