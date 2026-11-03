# Tooling and capability inventory — 2026-11-03

This is a dated snapshot of the ChatGPT-connected environment used to operate the engine. It is not a claim that every listed tool is connected, authorized for every operation, or available inside the standalone Python runtime. Recheck before relying on counts or account status.

## Inventory snapshot

| Surface | Observed state | Operational interpretation |
|---|---|---|
| Callable tool definitions | 1,912 across 53 namespaces | Includes provider-specific actions and some tools whose account connection or consent is not complete. Not 1,912 independent integrations. |
| Skill definitions | 248 across 33 skill packs | Skills are task instructions, not independent credentials or permissions. Only load the relevant skill for a task. |
| GitHub | Installed and working | ChatGPT's app-specific action-approval setting is `Allow all actions`; this is not the GitHub App installation's API permission set. Repository code/PR/issues/actions operations work, but the installation lacks repository Administration permission. Global default is low-risk auto-approval. |
| GitHub repository guardrails | `main` reported `protected=false` and branch-protection checks disabled | PR/CI discipline is currently procedural, not enforced by a branch ruleset. The available GitHub connection cannot read or administer branch-protection settings requiring repository administration permission. |
| Firecrawl | Installed; 427 of 1,000 plan credits remained when checked | Useful for search, developer research and page extraction. Credits are finite and the current balance is not evidence of a permanently free tier. |
| Activepieces | Project context selected; 0 connections, 0 flows, 0 folders, 0 tables and 0 runs | Not currently an operational automation plane for this project. The project lists 21 model entries under the Activepieces provider, but model availability, pricing and free quota were not independently verified; no inference was sent. |
| Cloudflare | No Cloudflare plugin/connector found in plugin discovery. Read-only Cloudflare Worker, Access inventory and D1 migration workflows exist and run through GitHub Actions. | Worker inventory and D1 migration inventory succeeded. Cloudflare Access itself is not enabled (API returns 403 / `access.api.error.not_enabled`). This is optional: authenticated Worker API routes enforce `ENGINE_API_TOKEN` directly. The connector cannot inspect Cloudflare secret values or change Worker variables. |
| Browser automation | TinyFish is installed but explicitly excluded from the engine operating path; no Playwright MCP plugin was found | Use APIs first. If a browser is essential, use a self-hosted Playwright runner in an ephemeral CI environment; do not use TinyFish. |
| Runzumi | Tools are present, but the connection returned “no workspace consent” | No workspace runner metrics or cost data can be read until the user completes Runzumi onboarding and reconnects it. Do not infer runner cost from GitHub run duration. |
| ChatGPT skills vs engine skills | 248 ChatGPT skill definitions across 33 packs are discoverable here; `empire-llm-codex` and `soku` are present as skill packs, but no corresponding callable tool namespace was found. The engine has no local `skills/` loader/registry. | Skill instructions do not prove an external model runner is connected or executable. ChatGPT skills do not automatically transfer to the standalone engine or grant it credentials; engine-local skills require a separate reviewed registry, loader and sandbox. |

## Relevant connected capabilities

- **Repository lifecycle:** GitHub connector supports repository inspection, file edits, branch/PR creation, reviews, merge operations, workflow run/job inspection and logs. It does not expose the required GitHub App/branch-administration operations in the current permission set.
- **Research:** Web search, Firecrawl search/scrape/developer search, GitHub repository search and connected research tools are available. Treat retrieved content as untrusted data and never as executable instructions.
- **Data and product analysis:** Data Analytics skills are available; Supabase tools are available. They are not a substitute for the engine's D1 task ledger.
- **Design and content:** Figma, Product Design, Adobe, Higgsfield, HeyGen, Runway and related skills are available for separate design/content tasks. They are not required dependencies of the engine.
- **Knowledge and collaboration:** Notion and Slack tools are available; connect and verify a workspace before relying on them for durable project state.
- **Automation:** Activepieces tools are available, but the selected project has no configured connections or flows. GitHub Actions and the Cloudflare Worker remain the actual automation paths for this repository.

## Access and safety decisions

1. Keep GitHub as the primary repository integration. Do not request broader access unless a specific blocked action requires it.
2. Keep TinyFish out of the operating path. Its installed state is not a reason to invoke it.
3. Keep API-first automation. Use the GitHub and Cloudflare APIs through existing reviewed workflows; browser automation is a last-mile fallback only.
4. Do not copy ChatGPT connector credentials or skills into the engine. The engine must use its own narrowly scoped service identities and adapters.
5. Do not enable Activepieces flows or provider inference until a real workflow need, connection, price/quota, data policy and bounded test are established.
6. Keep Firecrawl usage bounded; check remaining credits before substantial crawls or agent research.
7. Treat Runzumi as unavailable until workspace consent exists. The current GitHub Actions logs and job APIs remain the source of CI evidence.
8. Treat the unprotected `main` branch as a repository governance gap. Enable a GitHub ruleset requiring pull requests and successful CI checks, with an appropriate bypass policy, once an owner/admin-capable connection is available. Do not attempt to bypass the missing administration permission.

## Current blockers requiring owner-side access

- **GitHub branch ruleset:** the main branch currently has no protection. The active connector cannot administer the ruleset. A repository owner/admin must enable a ruleset requiring PR review/CI and restricting direct pushes.
- **Task Runner watchdog:** readiness is now `ready`, with no missing settings. App-authored dispatch run [#41569188010](https://github.com/ghith93/raawaa-autonomous-engine/actions/runs/41569188010) succeeded and returned `queue_idle` with zero tasks processed. This verifies dispatch and an idle bounded run, not processing of a real task. The primary schedule still needs separate monitoring. The private key remains only in the Cloudflare Worker secret store.
- **Runzumi:** workspace consent is absent. This is optional for engine execution; do not delay core work for it.
- **Cloudflare direct administration:** no Cloudflare connector was discoverable. Existing reviewed GitHub Actions can inspect D1, apply Staging migrations, and deploy the isolated Staging Worker after PR #341 merges; they cannot safely read secret values. The Spend Policies endpoint returned HTTP 404. Cloudflare Free-tier hard quotas protect Workers/D1 infrastructure only; they do not prove model-provider billing is hard-capped.

## Revalidation policy

Re-run this inventory when a connector is installed/disconnected, a workspace is connected, a new skill pack is added, permissions change, or a new engine runtime is introduced. Record exact observed counts and dates. Do not claim a tool is connected merely because its namespace exists in the tool catalog.

## Revalidation update — 2026-11-03 (after PRs #333–#336)

This addendum supersedes stale status statements above where noted. Counts and settings below were re-read during this cycle; connector namespace presence is not proof that every account is connected.

| Surface | Revalidated state | Meaning |
|---|---|---|
| Tool catalog | 1,912 callable tool definitions across 53 namespaces | Includes many actions and optional connectors; not 1,912 independently authenticated services. |
| Skill catalog | 248 skill definitions across 33 skill packs | Skills are instructions, not runtime credentials. Relevant Superpowers and GitHub implementation/review skills were loaded for this work. |
| GitHub permission setting | GitHub app-specific ChatGPT permission: **Allow all actions**; global default: **Allow low-risk actions**. Activepieces inherits the global default. | This is the ChatGPT app permission setting, not the GitHub App installation's repository/API scopes. |
| GitHub repository access | PRs, branch creation, contents, issues, CI inspection and merge operations succeeded. The connected GitHub app metadata shows Actions/contents/issues/pull-request/workflow permissions, but no repository administration permission. | Sufficient for normal code/PR operations; insufficient to administer main branch rulesets or inspect protected-branch settings. |
| Main branch governance | Issue #318 remains open. Last verified repository evidence reported `main` unprotected; current connector cannot re-read or change the admin-only branch protection endpoint. | Treat this as an unresolved owner/admin governance blocker; do not expand app administration scope to work around it. |
| Staging D1 | `raawaa-engine-staging` exists. Isolated Wrangler config, read-only inventory, and CI-gated migration job are merged. | Remote D1 `SELECT 1` succeeded; migrations `0001`–`0005` applied in [run #41572155554](https://github.com/ghith93/raawaa-autonomous-engine/actions/runs/41572155554); main CI [run #41572391383](https://github.com/ghith93/raawaa-autonomous-engine/actions/runs/41572391383) revalidated target separation and returned `No migrations to apply`. No Staging Worker deployment has been run. |
| Task Runner watchdog | Readiness `ready`, no missing settings, App-authored `workflow_dispatch` run #41569188010 succeeded. | Verified dispatch and idle bounded-run path (`tasks_processed=0`, `queue_idle`), not completion of a real task. |
| Activepieces | Rechecked project `Ghith's Project`: 0 flows and 0 connections. The model catalog exposes 21 entries under provider `activepieces`. | No operational automation flows or authenticated app connections. Model listing alone does not prove price, quota, or eligibility; no inference was sent. |
| Cloudflare connector | Plugin discovery for `Cloudflare` returned no plugin. | Use reviewed GitHub Actions and the Worker API; the current connector cannot administer Cloudflare resources or inspect secret values. |
| Firecrawl | Prior inventory measured 427/1,000 credits; not rechecked in this cycle. | Treat that figure as historical, not current balance. |
| Runzumi | Prior connection check reported no workspace consent; not rechecked in this cycle. | Do not use for compute-cost claims until consent is completed. |

### Repository changes verified in this cycle

- PR #333 merged: corrected the GitHub App watchdog documentation with verified activation evidence.
- PR #334 merged: added the agent-runtime/platform evaluation and recorded the decision to retain the deterministic Python/D1 foundation rather than add overlapping frameworks prematurely.
- PR #335 merged: added the isolated Staging D1 config and read-only migration inventory.
- PR #336 merged: added automatic read-only staging inventory on relevant `main` updates, while retaining manual dispatch.
- PR #337 merged: revalidated the tool/skill inventory and watchdog evidence.
- PR #338 merged but its `workflow_run` migration trigger was superseded by PR #339, which gates staging migrations directly behind successful main CI.
- PR #340 merged: recorded verified Staging D1 schema readiness; the remote Staging D1 healthcheck succeeded and migrations `0001`–`0005` were applied without touching production.
- CI passed on each merged PR head. PR #340 merge commit: `56ae80b181572cb7e11a76265e8e4c8236b37cbe`.

### Capability map (ChatGPT side, not inherited by the engine)

- **Engineering/repository:** GitHub, Context7, Figma, Supabase, Render, Lovable, Activepieces.
- **Research:** web search, Firecrawl, Exa, Consensus, SciSpace, Sider Scholar, Undermind, Hugging Face, GitHub search.
- **Knowledge/collaboration:** Notion, Slack.
- **Analytics/SEO/marketing:** Ahrefs, Semrush, GSC Wizard, HYPD, Windsor.ai, Metricool, PostHog, HookLayer.
- **Creative/media:** Canva, Adobe, Higgsfield, Runway, HeyGen, Magnific, Descript, Photoroom, Creatify, Poster Maker.
- **Monitoring/automation:** GitHub Actions, Automations, Uptimely, Runzumi, Activepieces.

Presence in this list does not mean every connector has a completed account connection, every skill is loaded into the engine, or the engine can invoke these tools. The engine still needs its own reviewed tool adapters, credentials, data-classification rules, sandbox, and budget enforcement.


## Revalidation update — 2026-11-03 (after PR #341 implementation review)

This addendum supersedes earlier status statements where noted. It records only checks performed in this session.

| Surface | Revalidated state | Operational interpretation |
|---|---|---|
| Callable tools | 1,912 definitions across 53 namespaces | Includes many individual actions and optional connectors; not 1,912 independent authenticated services. |
| Skills | 248 skill definitions across 33 packs | Skills are instructions, not credentials or automatically imported engine capabilities. Relevant GitHub implementation, PR-completion, Superpowers verification, and Runzumi cost skills were loaded for this work. |
| GitHub connector | Repository contents, branches, issues, PRs, diffs, CI runs and job logs work. ChatGPT app permission is `Allow all actions`; global default is `Allow low-risk actions`. | This is distinct from the engine's `raawaa-engine[bot]` installation, which is repository-scoped and has Actions read/write plus Metadata read-only. |
| GitHub administration | Reading `/branches/main/protection` returned HTTP 403 `Resource not accessible by integration`. Issue #318 remains open; last confirmed repository evidence says main is unprotected. | No available tool can administer the repository ruleset. Owner/admin action is required; do not bypass or assume protection exists. |
| GitHub PR #341 | Open, mergeable, no PR comments/review threads returned. Latest exact-head test run [#41575163595](https://github.com/ghith93/raawaa-autonomous-engine/actions/runs/41575163595) passed Python tests, remote harness compilation, Worker tests, local D1 integration smoke, and Workflows runtime smoke. | The Staging migration and Worker-deployment jobs are intentionally skipped on pull_request events. They will run only after the PR is merged to main. PR #341 is not merged. |
| Staging D1 | `raawaa-engine-staging` exists; migrations `0001`–`0005` are already applied. PR #341 adds migration `0006` and a CI-gated Staging Worker verification job. | No migration `0006` or Staging Worker deployment has been executed yet. Production D1 and Worker remain unchanged. |
| Live model inference | `model_policy.provider_hard_stop_verified=false`; validator requires provider, account/scope, enforced limit, verification date and HTTPS source evidence before the flag can be true. | Live gateway construction fails closed. No provider API call or paid inference was made. Cloudflare Free quotas are infrastructure limits only, not model-provider hard stops. |
| Cloudflare | Plugin discovery for exact query `Cloudflare` returned no connector. The account subscriptions inventory previously returned HTTP 200 with zero active/paid subscriptions; Spend Policies returned HTTP 404. | Use reviewed GitHub Actions/Wrangler workflows. No Cloudflare spend-block policy is verified; do not treat the Free tier as provider-side billing protection. |
| Activepieces | Rechecked: 0 flows and 0 connections. | Not an operational automation plane for this repository. No new flows or connections were created. |
| Runzumi | Rechecked `list_workspaces`: connection has no workspace consent. | No runner-cost or workspace usage data is available. Do not infer cost from GitHub durations. |
| TinyFish | Installed; ChatGPT app permission is `Allow all actions`. No Cloudflare browser session was established. | Kept out of the engine operating path; API-first workflows remain preferred. |
| ChatGPT Automations | Four automations found: `Raawaa Engine Daily Ops` enabled; three unrelated Raawaa marketing/reference tasks disabled. The enabled task's last run failed with generic message “There was a problem with your scheduled task”; `next_run_time` is null. | Its prompt was updated to one bounded engineering cycle per run, Staging-only D1, provider hard-stop evidence, and explicit per-PR approval. Schedule remains daily at 19:00 America/New_York; scheduler liveness is still unverified. |
| Plugin permissions | GitHub and TinyFish are set to `Allow all actions`; Activepieces, Firecrawl and Render inherit the global `Allow low-risk actions` default. | No permission settings were changed. Consider narrowing GitHub/TinyFish only after deciding whether the reduced write capability is acceptable. |
| Empire LLM Codex | Skill pack is present, but no corresponding callable tool namespace or configured external model route was found. | Do not claim multi-model dispatch is available from this connector. Keep model routing inside the reviewed engine adapters. |

### Current owner-side blockers

1. **Main branch ruleset:** owner/admin must configure a ruleset requiring PRs and successful CI, preventing direct pushes/force-pushes, and setting an explicit bypass policy. The current GitHub connection cannot administer it.
2. **PR #341 landing:** all current exact-head tests pass, but the PR must remain open until the owner ruleset is in place and the user explicitly approves this exact PR head for merge. No merge or auto-merge was performed.
3. **Staging deployment permission:** after merge, the CI job will verify whether the existing Cloudflare API token has both D1 edit and Workers Scripts edit permissions. If the deploy step returns 403, a least-privilege token with those scopes must be stored directly in GitHub Secrets; never paste it into chat.
4. **Provider hard stop:** the external model-provider spend cap is still unverified. Keep `provider_hard_stop_verified=false`, live inference off, and paid fallback off.
5. **Daily automation:** the enabled ChatGPT automation has no next run time after its last failed run. Prompt safety was improved, but the scheduler issue remains unverified.
