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


## Verified 2026-11-03: separate install, authentication, and execution gates

The existing Codespaces bootstrap installs Codex CLI and Antigravity CLI alongside OpenCode. Do not collapse the following independent states into a single ready flag:

| Gate | Codex CLI | Antigravity CLI |
|---|---|---|
| Binary installation | pinned npm package in `.devcontainer/post-create.sh` | official Linux installer in `.devcontainer/post-create.sh` |
| Account authentication | ChatGPT OAuth, device-code flow where enabled | Google account OAuth; remote SSH URL/code handoff |
| Identity proof | `codex login status` | authenticated CLI session plus successful `agy -p` |
| Usage/quota visibility | `/status` in an interactive Codex session and ChatGPT usage dashboard | `/usage` in the CLI; capture only documented aggregate usage metadata |
| Headless execution | `codex exec` | `agy -p` |
| Engine integration | not implemented | not implemented |

### Codex account usage
- ChatGPT-account authentication uses the user's ChatGPT plan allowance; it is not OpenAI API billing.
- API-key authentication is a different billing path and must not be used as a fallback for this zero-additional-spend pilot.
- The exact Free-plan task allowance varies and is not a fixed token quota. Record the actual `/status` output's allowance/reset information as a dated operator observation; do not scrape private account endpoints or infer an exact quota from the plan name.
- Keep the OAuth credential in the Codespace user's protected home/credential store. Never copy `~/.codex/auth.json` into GitHub Secrets, repository files, artifacts, issues, or logs. Treat it as a password.
- A GitHub Actions runner must not be seeded with a personal ChatGPT session. The public repository and CI workflow are not a trusted persistent personal-auth runner.

### Antigravity account usage
- Use the Google account OAuth route, not `GEMINI_API_KEY`, for this experiment. A Gemini API key is separately billed/limited and is not equivalent to a Google-account Antigravity entitlement.
- For a Codespace/SSH terminal, use the documented remote OAuth URL and one-time authorization code flow; do not export local keyring files or copy Google tokens.
- Use `agy -p` only after authentication. The CLI documents JSON/stream-JSON output and a `/usage` command. Capture aggregate usage counters, selected model, timestamps, and status only; redact account identifiers and never persist prompt/response content in quota telemetry.
- Review Google's interaction-data collection and account terms before sending private project material. Initial pilot tasks must be public-classified only.

### Headless safety defaults
- Codex: use `codex exec --sandbox read-only` for review/research and `--sandbox workspace-write` only for a bounded implementation task. Never use `danger-full-access` or `--full-auto` in the engine runner.
- Antigravity: use `agy -p ... --output-format json --sandbox` and a short explicit timeout. Do not use `--dangerously-skip-permissions`. Do not grant blanket shell-command permissions.
- Both CLIs are execution engines, not generic chat APIs. The engine must wrap each invocation with a durable task ID, lease fencing, wall-clock timeout, bounded stdout/stderr, process-group termination, workspace isolation, and metadata-only audit.
- The engine's D1 provider quota ledger currently accounts API provider requests/tokens, not ChatGPT-plan or Antigravity-account allowances. Do not route tasks to either CLI until a separate account-usage budget/circuit breaker is designed and verified.
- Never start both agents on the same working tree. Use isolated Git worktrees per task and let a distinct reviewer inspect the resulting diff before a PR can be merged.

### Current gate result
- Install/bootstrap definition: present.
- Linux install/version smoke: must pass on the exact current branch before calling the binaries install-verified.
- Codex ChatGPT OAuth: not yet completed or verified in a persistent Codespace.
- Antigravity Google OAuth: not yet completed or verified in a persistent Codespace.
- Headless inference: not yet run.
- Model Router/Execution Manager integration: not yet implemented.
- Production or storefront write permissions: not granted by this plan.
