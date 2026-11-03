# Cloud coding-agent workspace (GitHub Codespaces)

## Purpose

The repository now includes a repeatable GitHub Codespaces development environment for running real coding-agent CLIs from a browser-accessible Linux workspace. It is designed for the operator's Android tablet or iPhone; no local PC, Android SDK, npm installation, or SSH server is required.

Configuration:
- `.devcontainer/devcontainer.json`
- `.devcontainer/post-create.sh`

The container uses the standard Codespaces base image plus Node.js 22 and Python 3.12 features. It installs:
- OpenCode CLI `2.2.5`
- OpenAI Codex CLI `0.166.0`
- Google Antigravity CLI (latest checksum-verified release at creation time)

The setup script makes no model requests, does not authenticate, and does not read or create API keys.

## Free-tier boundary

GitHub's official documentation currently lists personal GitHub Free accounts with **120 Codespaces core-hours and 15 GB-month storage** per billing month. A 2-core Codespace consumes two core-hours per active hour, so the compute allowance corresponds to up to 60 active hours on a 2-core machine. This is a quota, not unlimited hosting.

- Create a 2-core Codespace.
- Keep the default idle stop behavior or reduce the timeout; stop the Codespace when finished.
- Check current usage before creating it.
- If a payment method is attached, ensure Codespaces overage spending is blocked with a zero budget. If no payment method is attached, GitHub blocks billable use after the included quota is exhausted.
- Do not configure prebuilds or larger machines for this workspace.
- A Codespace is not a 24/7 always-on server. It stops on idle and can be resumed later.

Official billing references:
- https://docs.github.com/en/billing/concepts/product-billing/github-codespaces
- https://docs.github.com/en/codespaces/troubleshooting/troubleshooting-included-usage

## Create the workspace

After this configuration is merged to `main`:

1. Open https://github.com/codespaces/new?repo=ghith93/raawaa-autonomous-engine
2. Select branch `main` and the 2-core machine.
3. Confirm that GitHub shows included usage / no billable overage before creating it.
4. Wait for the post-create script to finish. The terminal should show versions for all three CLIs.

## Authenticate Codex with the ChatGPT Free plan

Codex is included across ChatGPT plans, including Free and Go, but usage limits vary by plan. Codex Cloud is not included with Free or Go; this workspace runs the **local Codex CLI process inside the Codespace**, not Codex Cloud.

For a remote terminal, the preferred documented route is device-code authentication (beta):

1. Enable device-code login in ChatGPT Security settings if it is not already enabled.
2. In the Codespaces terminal, run `codex login --device-auth`.
3. Open the displayed URL on the tablet/phone, sign in to the same ChatGPT account, and enter the one-time code.
4. Verify with `codex login status`.
5. Check current allowance with `/status` in an interactive Codex session or the ChatGPT usage dashboard.

Do not create an OpenAI API key for this setup. API-key authentication uses separate usage-based billing and is not part of the zero-additional-spend design.

Official references:
- https://help.openai.com/en/articles/11369540-using-codex-with-your-chatgpt-plan
- https://learn.chatgpt.com/docs/auth
- https://learn.chatgpt.com/docs/non-interactive-mode

## Run a real coding task

From the repository root, use a bounded task prompt and workspace-write sandbox:

```bash
codex exec --sandbox workspace-write "Read AGENTS.md and the required policy documents. Inspect the current repository state. Implement one highest-priority safe improvement, add tests, run the relevant test suites, and report changed files and evidence. Do not modify storefront repositories, reveal secrets, enable paid inference, or bypass existing policy gates."
```

The Codex CLI runs as a local process in the Codespace and is subject to the ChatGPT Free plan's Codex limits. `codex exec` is non-interactive; it does not provide unlimited model usage. The `workspace-write` sandbox limits file changes to the workspace. Network and external side effects remain separately constrained.

OpenCode and Antigravity are installed as alternative CLI front ends. They do not inherit ChatGPT Free entitlements automatically; only connect them to a provider/account after verifying that provider's exact free quota, terms, data handling, and billing controls. Never assume an open-source CLI means free model inference.

## Operational relationship to the engine

- Codespaces is the interactive coding-agent environment.
- GitHub Actions remains the bounded CI and scheduled task consumer.
- Cloudflare Worker + D1 remain the control plane and durable task ledger.
- The Python `engine.agent_runtime` remains deterministic until a dedicated, tested adapter is added. Installing a CLI does not automatically connect it to the task queue.
- Keep changes on a feature branch and create a PR. Merge only after exact-head CI and review.


### Antigravity headless use
After the interactive Google OAuth sign-in completes in the Codespace, the documented headless interface is `agy -p "Return only: ANTIGRAVITY_AUTH_OK" --output-format json --sandbox --print-timeout 60s`.

A successful process exit and a JSON status of `SUCCESS` prove a single account-backed prompt worked. It does not prove a remaining quota amount or unlimited use. Do not pass `--dangerously-skip-permissions`.

### Codex device-code sign-in
In the Codespaces terminal, run `codex login --device-auth` and then `codex login status`. Complete the displayed one-time flow in the browser. Then launch an interactive Codex session and use `/status` to inspect the current account allowance and reset information. Do not use an API key for this test.

### Minimal authentication verification task
Once both logins are complete, run only these low-impact checks: `codex exec --ephemeral --sandbox read-only "Return only: CODEX_AUTH_OK"` and `agy -p "Return only: ANTIGRAVITY_AUTH_OK" --output-format json --sandbox --print-timeout 60s`.

These checks send one trivial prompt to each provider and therefore consume their account allowances. Do not run them until you have reviewed the current usage display. Record success/failure, model ID, elapsed time, and aggregate usage metadata; do not store the prompt/response, credentials, or account identifiers in repository artifacts.
