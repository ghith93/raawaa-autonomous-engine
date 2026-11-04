# Browser-free automation and Worker deployment

## Decision

TinyFish is removed from the Raawaa Autonomous Engine operating path. Do not use it for repository operations, deployment, or routine automation.

The primary replacement is **API-first execution through the existing GitHub integration and GitHub Actions**, not another hosted browser vendor. This removes browser-session credits and keeps the deployment credentials inside GitHub Actions secrets.

## Worker deployment without a paid browser

`.github/workflows/deploy-worker.yml` has a single guarded entry point:

1. A browser-free `workflow_run` hand-off after **Engine CI** completes successfully for a push to `main`, but only when the resulting main commit message contains the exact marker `[deploy-control-worker]`.

The manual `workflow_dispatch` route was removed deliberately: any credential holding `actions: write` can dispatch a workflow, so a dispatch is not an authorization boundary. `scripts/check_workflow_dispatch_guards.py` enforces this in CI.

The remaining route is deliberately narrow:
- Pull-request CI runs do not deploy.
- Ordinary pushes to main do not deploy.
- Failed or cancelled CI runs do not deploy.
- The deploy job checks out the exact successful CI head SHA, not a moving branch tip.
- The same three deployment secrets are validated before any Worker secret update or deploy command.
- The workflow's concurrency group prevents overlapping Worker deployments.

To request one guarded rollout through the normal reviewed code path, use a PR title containing `[deploy-control-worker]`. Merge only after exact-head CI passes and review findings are clear. The merge creates a push to main; Engine CI runs first; the deployment workflow is eligible only after that CI succeeds. This uses GitHub's native event/API pipeline and does not require a browser agent, personal token in source, or paid automation credits.

Keep production D1 migrations separate. This workflow does not apply migrations, enable live inference, activate trading, configure Telegram, or perform financial actions.

## Browser automation choices

Use browser automation only when a task genuinely requires a visual browser or a site has no suitable API.

| Tool | License / cost model | Decision |
| --- | --- | --- |
| [Playwright](https://github.com/microsoft/playwright) and [Playwright MCP](https://github.com/microsoft/playwright-mcp) | Open source; self-hosted/local runner, no browser-platform subscription | Preferred for deterministic browser tests and known UI flows. Run in GitHub Actions when needed; runner minutes and browser dependencies still have operational limits. |
| [Browser Use](https://github.com/browser-use/browser-use) | MIT-licensed library; local/self-hosted browser is free, hosted cloud and model inference are metered | Keep as a future option for agentic browser tasks; not needed for GitHub or Cloudflare deployment. |
| [Stagehand](https://github.com/browserbase/stagehand) | MIT-licensed SDK; browser/model hosting can incur usage costs | Do not add now; overlaps Playwright and adds an AI/model dependency. |
| [Skyvern](https://github.com/Skyvern-AI/skyvern) | Open source with self-hosted operational requirements; hosted plan is metered | Defer; heavier than the current deterministic needs. |
| Browserbase / Steel hosted browsers | Hosted browser infrastructure is metered; Steel can also be self-hosted | Do not adopt as a default; unnecessary cost and operations for current workflows. |

Open-source license does not make compute, model inference, proxy, or hosting costs disappear. Prefer direct authenticated APIs over UI automation; use Playwright for repeatable browser actions; use Browser Use or Skyvern only when a task truly requires agentic visual navigation.

## Validation and rollout evidence

Before treating the new hand-off as operational:
- Validate the workflow YAML and event filters in CI.
- Confirm a normal PR CI run cannot deploy.
- Confirm an ordinary main push without the marker cannot deploy.
- Confirm a successful main push with the exact marker triggers one deployment.
- Confirm the deployment checks the expected head SHA and reports the deployed Worker version.
- Verify the authenticated health/status endpoint after deployment.

A workflow definition or passing static validation is not proof of a production deployment. Record the actual run ID, commit SHA, Worker version, and post-deploy status check before reporting success.
