# Raawaa Engine — Asset and Capability Register

**Snapshot:** 2026-11-03 21:45 UTC  
**Scope:** Known connected accounts, project assets, permissions, secret names, reusable workflows and runtime state. Revalidate at the start of every session; never treat tool-catalog presence as proof of account access.

## Startup rule

Before doing work, read `AGENTS.md`, the permanent directive, continuous-improvement charter, architecture, operations guide, this file, `docs/operations/tooling-capability-inventory-2026-11-03.md`, `config/integrations.json`, and `config/tool_permissions.json`. Then inspect current main SHA, PRs/CI, workflow tree/runs, Activepieces connections/flows, automations, and relevant runtime status. Search for an existing mechanism before creating another. Keep this register updated with timestamp and evidence. Record secret names only; never store or expose values.

## Projects and infrastructure

| Asset | Current evidence | How to use / boundary |
|---|---|---|
| Engine repo | https://github.com/ghith93/raawaa-autonomous-engine; main SHA `168c5f35ec166b6c30fb60171fd3893860c37b85` | Primary project. Use GitHub connector for code, PRs, CI and Actions. |
| Raawaa storefront | https://raawaa-store.pages.dev | Separate site/repository. Do not modify during engine work. |
| Production Worker | `raawaa-autonomous-engine-control`; https://raawaa-autonomous-engine-control.ghith-shikhkhaled.workers.dev | Production deployment #41723367620 succeeded; latest later status shows watchdog activation missing. |
| Production D1 | `raawaa-autonomous-e`; ID `f0ffbed3-940f-488d-a7d1-31cbc5fbf742` | Durable production task ledger. No production migrations in this cycle. |
| Staging D1 | `raawaa-engine-staging`; ID `231dd948-04a0-49e3-ad73-444f77d69477` | Use for schema changes and remote verification. |
| Supabase | Org `Ghaiooth`; project `Ghaioooth-core`, ref `oyoyozdugicmnkfhjovo`, region `eu-west-1`, `ACTIVE_HEALTHY` | Separate project; inventory only, no changes. |
| Notion | Workspace `Ghaith’s Space`; account access verified | Basic page/search/fetch available. Cross-source connector search and some database features are plan-limited. |
| Render | Workspace `My Workspace`; service listing returned `null` | No deployed service observed. Do not provision paid services. |

The current GitHub connector cannot enumerate every repository owned by the account. This is the known project list, not a complete account-wide repository inventory.

## Connected integrations and permissions

| Integration | Verified state | Permission / limitation |
|---|---|---|
| GitHub | Working: repo files, branches, PRs, Actions, logs and rulesets | ChatGPT app-specific `Allow all actions`; global `Allow low-risk actions`. These are not API scopes. |
| Activepieces | Project `Ghith's Project` (`8A2qoCJNq9zlORJZMlrfw`); 1 ACTIVE GitHub connection (`cn2iEAi2bWbLfeOVOu7D8`) | App-specific `Allow all actions`. Reuse existing connection. |
| Activepieces flow | `Raawaa GitHub Workflow Control`, ID `oDKKtoRanykDWtyBdkQFt`, enabled/published | Manual Trigger → GitHub workflow_dispatch of `task-runner.yml` on `main`; waits up to 30 min, polls every 60 sec. One TEST run succeeded. 0 tables. |
| Activepieces AI catalog | 20 models under provider `activepieces` | Pricing/quota/eligibility unverified; do not treat as free or use for inference without verification. |
| Firecrawl | Authenticated; 418/1,000 credits; billing period 2026-10-29 to 2026-11-29 | Global low-risk permission. Finite credits; check balance before large crawls. |
| Slack | Workspace list returned empty | Treat as unconnected/unverified. |
| Canva | Brand Kit list empty | Design tools available; no Brand Kit found. |
| GSC Wizard | No Search Console properties connected | No property data available until connected. |
| Runzumi | Workspace lookup returned “no workspace consent” | Unavailable for runner metrics/cost until consent + reconnect. |
| TinyFish | Not installed | Keep excluded from engine path. |
| Notion | Workspace metadata and tool-access map returned | Connector search across external sources is plan-required. |
| Supabase / Render | Read-only project/workspace discovery succeeded | Supabase project listed above; Render returned no service records. |

Global ChatGPT permission is `Allow low-risk actions`. Firecrawl and Render inherit it. GitHub and Activepieces have app-specific `Allow all actions`. Tool definitions do not mean the engine can call them.

## Tool and skill catalog

At this snapshot: **1,889 tool definitions / 49 MCP namespaces** and **250 skill definitions / 34 packs**. Namespace presence is catalog availability, not account authentication. The complete namespace list and earlier capability map are in `docs/operations/tooling-capability-inventory-2026-11-03.md`. Relevant skill packs include data-analytics, Figma, Product Design, Notion, Sales, Supabase, Superpowers, PR Completion, Plugin Management, Runway, Runzumi, Empire LLM Codex and Soku. Load skills when relevant; they do not grant credentials.

## Secret names only

Workflow references include: `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`, `ENGINE_API_TOKEN`, `ENGINE_QUOTA_ADMIN_TOKEN`, `GROQ_API_KEY`, `GEMINI_API_KEY`, `CF_ACCESS_CLIENT_ID`, `CF_ACCESS_CLIENT_SECRET`, `CLOUDFLARE_ACCESS_AUDIT_TOKEN`. Only the production deployment explicitly verified Cloudflare API token, account ID and engine API token were present in run #41723367620. Other names are references until individually verified.

Cloudflare Worker watchdog secret names: `GITHUB_APP_ID`, `GITHUB_APP_INSTALLATION_ID`, `GITHUB_APP_PRIVATE_KEY`. Separate dashboard variable: `GITHUB_APP_WATCHDOG_ENABLED=enabled`. The latest independent status reported that activation variable missing. ARTEMIS required environment names are `ARTEMIS_BASE_URL`, `ARTEMIS_TOKEN`, `ARTEMIS_REMOTE_AUTH_VERIFIED`; ARTEMIS remains disabled and no daemon/device was verified.

## Reusable workflows — inspect before creating new ones

- `task-runner.yml`: bounded D1 Task Runner; scheduled/manual; one-shot public research input.
- `deploy-worker.yml`: Production Control Worker deployment; manual input `confirm_deployment=DEPLOY_CONTROL_WORKER`.
- `cloudflare-access-inventory.yml` and `cloudflare-access-audit.yml`: read-only Access inventory/audit.
- `cloudflare-worker-inventory.yml`: read-only Worker inventory and health.
- `cloudflare-billing-inventory.yml`: read-only subscriptions and spend-policy inventory.
- `cloudflare-workers-ai-inventory.yml`: read-only model catalog; no inference.
- `d1-staging-inventory.yml` and `d1-migration-inventory.yml`: read-only Staging/Production migration inventory.
- `groq-connection-check.yml`: read-only Groq auth/catalog check.
- `groq-free-inference-staging-smoke.yml`: one bounded Groq Free request on isolated Staging; ephemeral bearer token is revoked. Push-only when its script is deliberately changed.
- `research-discovery.yml`: scheduled public GitHub/Crossref/arXiv metadata discovery.

## Current verified runtime state

- GitHub ruleset `main-protection` ID `26464558` is active: PRs required, exact `test` status required, strict up-to-date checks, review-thread resolution, deletion/non-fast-forward blocked, no bypass actors.
- PR #431 merged as main SHA `168c5f35ec166b6c30fb60171fd3893860c37b85`; exact-head CI #41724349759 passed. Staging migrations applied; isolated Staging Worker/monetary-ledger job was still in progress at last inspection.
- Production Worker deploy #41723367620 succeeded. Later Task Runner status #41724349887 returned HTTP 200 but `watchdog_ready=not_ready`, missing `GITHUB_APP_WATCHDOG_ENABLED`. Treat watchdog as disabled until corrected deploy and a fresh status read returns ready/none.
- Task Runner #41724349887 completed health/auth/D1 probes and one bounded `dry_run` result; no live inference or external write.
- Cloudflare Access inventory #41723721082: Access is not enabled; no apps/tokens are applicable. Worker bearer-token auth remains the active API boundary.
- Cloudflare billing inventory #41723687960: subscriptions HTTP 200, zero subscriptions/active paid subscriptions; Spend Policies HTTP 404. No account-level usage-block policy is verified; keep overages disabled.
- `Raawaa Engine Daily Ops` automation is enabled but last recorded run failed and `next_run_time=null`. Its prompt was updated to require this register and inventory-first discovery; scheduler liveness is unverified.
- Repository defaults remain `safe_dry_run`; provider routes disabled by default; paid fallback and external writes disabled. No live Groq inference is claimed.

## ARTEMIS status

Google ARTEMIS is registered in `config/integrations.json`, but disabled and not runtime verified. The current safe probe is `python -m engine.cli artemis-status` (GET `/api/devices` only). Do not call `/api/status`, POST /run, ADB shell or device actions until the daemon, secure endpoint, authorized ADB device, model cost and scope are verified. See `docs/integrations/google-artemis.ar.md`.

## Maintenance contract

At every session start, refresh counts, account/connection status, app permissions, Activepieces project/flow/connection, automations, secret-name references, workflows, PR/CI, deployments and task/D1 status. Record each item as catalog-only, configured, connected, permission-verified, tested, merged, deployed, runtime-verified, blocked or unknown. Append dated evidence; never upgrade a status without proof.

 
## Continuation update — 2026-11-03 23:12 UTC

- New additive migration asset: `migrations/0008_task_retry_schedule.sql`. It adds nullable `tasks.next_attempt_at` and an index for due-task selection. Existing rows remain immediately eligible because NULL means no delay.
- F2 implementation branch `fix/provider-not-called-backoff` updates Python SQLite task scheduling, the remote D1 task adapter, and the Control Worker claim/retry routes to share the same delayed-retry contract.
- Status: implemented on an unmerged branch; CI is being corrected/re-run. Migration 0008 is not applied to any remote database. No Worker deployment has occurred from this branch.
- Spec Kit remains a deferred optional developer tool; no dependency or runtime asset was added.

- F2 verification: workflow-dispatch CI `41737974145` passed; PR #444 was opened and PR-event CI `41738158606` passed on PR head `23a38b61a387549f33548a58d9815cccfe36d76f`, including local D1 delayed-retry smoke. PR #444 remains open/unmerged. Remote migration/deployment remains pending.

## F2 correction and developer-tool evaluation — 2026-11-04

- PR #444 merged at `a9c3273201cab33ea0193609b0377453ad194391`; exact PR-head CI run [#41738158606](https://github.com/ghith93/raawaa-autonomous-engine/actions/runs/41738158606) passed its test job. Staging migration/deployment jobs were skipped. No evidence shows migration 0008 or its Worker deployment reached Staging/Production.
- Review found unsafe classification of invocation checkpoint transport failures as retryable. Corrective work is on branch `fix/f2-capacity-preflight`: restore the previous queue/migration contract; use read-only pre-claim capacity checks; leave uncertain checkpoint/provider outcomes blocked; no replay.
- Preflight reuses quota eligibility and monetary budget checks, performs no network/provider I/O and creates no reservations. When no eligible verified-free route exists or global budget is exhausted, leave tasks pending and return idle. Gateway repeats the actual request check and atomically reserves before provider I/O. Per-task budget and exact prompt size remain checked after claim; races remain fail-closed.
- No new migration or D1/Worker deployment is planned for this F2 correction. No spend or policy limits changed.
- Graphify approved for a later code-only local evaluation: official source https://github.com/Graphify-Labs/graphify, Apache-2.0, official PyPI package `graphifyy`, CLI `graphify`. Offline code parsing is local; optional semantic extraction can send docs/media to the configured model backend. Not installed, not run, and not added as a runtime dependency. Security/privacy notes: https://graphify.com/security.
- Spec Kit remains a deferred optional MIT developer tool; no installation performed. F1/F5 remain pending.

## F2 completion — 2026-11-04

- PR #445 merged to main as `b20707d77dccb6157a4a9a90b6cb01cf12e0946a`; exact PR-head CI and post-merge main CI passed. The broad `ProviderNotCalled` classification and migration 0008 were removed; read-only capacity preflight now runs before live-mode task claim.
- F2 behavior: no eligible verified-free route or exhausted global budget → idle without claim/attempt consumption; actual prompt/quota/atomic reservation rechecked in gateway. Ambiguous checkpoint/provider outcomes stay blocked; no replay. No limits or paid fallback changed.
- Post-merge Staging pipeline passed: no migrations pending, isolated Staging Worker deployment and remote monetary/token-quota/invocation/redeploy checks passed, telemetry publish passed, authenticated mobile status readback passed. Ephemeral Staging bearer and quota-admin tokens were revoked.
- Production deploy workflow was skipped; Production Worker/D1 unchanged. No provider inference or external write.
- Issue #440 remains open; Codespaces executor remains disabled.
- F1 is next, then F5. Graphify (Apache-2.0, package `graphifyy`) is approved for later code-only local evaluation, not installed/run. Spec Kit (MIT) remains deferred.

## F1 execution-mode separation — 2026-11-04

- Introduces additive D1 migration `0008_task_execution_mode` and equivalent SQLite auto-migration. Legacy rows default to `unknown`; new claims record `dry_run`, `live_inference`, or `codespace`. Old clients without a mode remain compatible and record `unknown`.
- Mobile API/dashboard and Task Runner status now distinguish dry-run simulation from successful live inference/Codespace execution. Dry-run successes are not counted as executed; unknown legacy successes are not guessed.
- Migration/deployment is Staging-only in the normal post-merge pipeline; Production remains separately gated. No live inference, paid usage, external write, or Codespaces activation.

