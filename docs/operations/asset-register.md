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

- `task-runner.yml`: bounded D1 Task Runner; scheduled/manual; the legacy one-shot public research input fails closed pending independently verified zero-cost controls.
- `deploy-worker.yml`: Production Control Worker deployment; **no manual dispatch**; authorized only by a `main` push whose commit message contains `[deploy-control-worker]`. Wrangler is pinned to `4.156.1` for the D1 gate and the deploy, and the workflow **does not write Worker secrets**; a read-only `POST /v1/diagnostics/auth` preflight must first show the deployed Worker accepts the repository `ENGINE_API_TOKEN` (issue [#521](https://github.com/ghith93/raawaa-autonomous-engine/issues/521)).
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



## F1 execution-mode field — 2026-11-04

- Proposed additive migration: `migrations/0009_task_execution_mode.sql`; SQLite compatibility migration is in `engine/task_store.py`.
- Updated D1 Worker task list/claim/finish endpoints and Mobile Status to expose execution-mode aggregates.
- Values: `not_started`, `dry_run`, `live_inference`, `unknown`. Legacy rows remain `unknown`; new tasks begin `not_started`.
- Status: merged into `main` (PR #448); migration 0009 applied to Staging. PR #456 fixed guarded manual Staging telemetry publication. Post-merge run #41746574412 completed isolated Staging Worker deployment, remote ledger verification, telemetry publication, and authenticated mobile-status readback. Production Worker/D1 unchanged.

## F1 dashboard accuracy follow-up — 2026-11-04

- Mobile Status now separates successful live-inference tasks from successful dry-run simulations; unknown legacy successes are not included in executed totals.
- Task Runner status and Staging mobile verification expose aggregate execution-mode/status counts only.


## F5 priority and dependency queue — 2026-11-04

- PR #457 merged as `381e2094af5f6b47d56a812a6a544c8d6b23f13c`. Additive migration `0010_task_priority_dependencies.sql` applied to the isolated Staging D1 database; a follow-up migration list confirmed no pending migrations.
- Queue priority is bounded 0–100 (default 50) and ordered by priority descending, then creation time and task ID. SQLite and D1 enforce the same dependency edges, same-execution-mode success gate, transitive failure blocking, and audit events.
- Exact-head CI #41746324349 passed, including local D1 Worker smoke. Post-merge Engine CI #41746574412, Staging Worker deployment, remote ledger verification, telemetry publish, and authenticated mobile status readback all passed.
- Mobile readback: `status=verified`, `ci_health=success`, `kill_switch=engaged`, `external_writes=false`, `paid_model_calls=false`, `paid_fallback=false`.
- Ephemeral Staging bearer/quota-admin tokens were revoked. Production Control Worker deployment #41747019726 was skipped. No Production D1/Worker change, model-provider call, paid inference, or external write.
- Workflow planner remains proposal-only; no automatic workflow dispatch is enabled. Direct dependency behavior was integration-tested against local D1; no task was claimed from the shared Staging queue.


## Live revalidation — 2026-11-04 00:51 UTC

This addendum supersedes earlier snapshots where they conflict. Evidence was re-read from the live GitHub repository, Actions, Activepieces project, ChatGPT automation inventory, tool catalog, and current ruleset. Secret values were not accessed or recorded.

### Repository, governance, and CI

- Repository: ghith93/raawaa-autonomous-engine. Default branch: main. Exact main SHA: e6761d09de884d34bdc0ef7d01d7b7a8f1e1682e — https://github.com/ghith93/raawaa-autonomous-engine/commit/e6761d09de884d34bdc0ef7d01d7b7a8f1e1682e
- Current tree: 404 entries; 23 workflow YAML files under .github/workflows.
- Active ruleset main-protection, ID 26464558: active on the default branch; pull requests required; exact status context test required with strict up-to-date checks; review-thread resolution required; deletion and non-fast-forward updates blocked; no bypass actors. Important limitation: required_approving_review_count=0 and require_code_owner_review=false. The ruleset is active but does not enforce an approving review. The branch-protection endpoint returned HTTP 403 to this connector; the ruleset detail endpoint was readable.
- Main CI run #41747902547 passed on the exact main SHA. All five jobs succeeded: test, reviewed Staging migrations, isolated Staging Worker/D1 monetary verification, sanitized telemetry publication, and authenticated Staging mobile-status readback. https://github.com/ghith93/raawaa-autonomous-engine/actions/runs/41747902547

### Cloudflare, D1, and deployment evidence

- Staging D1 remains raawaa-engine-staging, ID 231dd948-04a0-49e3-ad73-444f77d69477. The latest main CI run validated target separation and deployed the isolated Staging Worker as version a78c3de1-1ee0-4892-b97c-c13ec54d99bc at https://raawaa-autonomous-engine-staging.ghith-shikhkhaled.workers.dev.
- Remote Staging verification passed quota concurrency/settlement, durable invocation identity, lease fencing, explicit 429 rotation, ambiguous replay blocking, global daily/monthly/task monetary caps, policy-tamper rejection, idempotency, pre-I/O release, ambiguous settlement, over-estimate quarantine, redeploy persistence, and aggregate-only authenticated status. No model provider was called. Temporary Staging secrets ENGINE_API_TOKEN and ENGINE_QUOTA_ADMIN_TOKEN were deleted by workflow cleanup.
- Production Control Worker deployment workflow run #41748314581 was skipped. Production Worker and Production D1 were not changed by this cycle. No Production migration or deployment occurred.

### Task Runner and durable queue

- Existing Task Runner workflow: .github/workflows/task-runner.yml. Latest run #41749347645 completed successfully on main SHA e6761d09de884d34bdc0ef7d01d7b7a8f1e1682e: https://github.com/ghith93/raawaa-autonomous-engine/actions/runs/41749347645
- Public Worker health GET, authenticated POST preflight, and authenticated D1 mobile-status read all returned HTTP 200. The bounded batch returned queue_idle with tasks_processed=0. D1 status reported tasks_pending=0 and tasks_running=0; execution-mode aggregates were unknown. This is an idle health/dispatch check, not completed business work or proof of live inference.
- Worker Cron heartbeat was recorded at 2026-11-04T00:45:34.193Z on */15 * * * *, with 0 stale leases requeued and 0 failed. The separate GitHub App watchdog remains not_ready because the non-secret Worker variable GITHUB_APP_WATCHDOG_ENABLED is missing. No variable or secret was changed.
- The run's safe-policy audit confirmed safe dry-run, three provider profiles, zero enabled provider routes, paid fallback disabled, infrastructure overages disabled, and external writes disabled. RAAWAA_ENGINE_LIVE_INFERENCE was unset; no provider request was made.

### Open PRs, blockers, and completed work

- PR #459 — draft security/trusted-main-only-codespace-bridge, head 6df513c40d96373028950235c7e0fb0ceb88a002, base main at e6761d09de884d34bdc0ef7d01d7b7a8f1e1682e. Exact-head CI #41749101378 passed. An automated security finding was posted as PR comment #6619451460: git status --porcelain --untracked-files=all omits ignored files, so an ignored executable/test artifact can remain outside the verified commit. Require a fresh tracked-only snapshot/worktree or an equivalent fail-closed ignored-file control plus regression tests. Keep draft/unmerged; the active ruleset still enforces zero approving reviews, and this automated comment is not an independent GitHub approval. Issue #440 remains open. Do not provision a token, enable the bridge, or start a Codespace.
- Other open PR heads observed: #443 70f61d94a1254ea58b784b5f995f6a57acee8385; #426 ecb1312b41c9c0fa3f89dba396477c9946be8e41; #423 89a8cd466804463e5ab992c4069f3c27e71cf21f; #421 48483820659197905cb58359bf38be92f7032e60; #405 48117a70c2453f9d5f608b944d2b127443d6e726; #380 06fe80031ad913d71a1e9a2cf808a526232fe7fa. These remain open/draft or stale-base work; no branch was updated or merged in this cycle.
- Issue #453 (F5 priority/dependency queue) was closed as completed after verifying PR #457, exact-head CI #41746324349, Staging migration/Worker verification in #41746574412, and the latest main CI. Closure: https://github.com/ghith93/raawaa-autonomous-engine/issues/453
- Issue #438 remains open for the separate workflow dispatcher contract and root-step/downstream release lifecycle. Workflow planning remains proposal-only. Issue #440 remains the Codespace security blocker. The main project tracking issue #34 remains open.

### Current ChatGPT and Activepieces capabilities

- Live ChatGPT tool catalog: 1,922 tool definitions across 64 namespace groups. The current skills__list call returned an empty list; no skill pack was surfaced in this runtime. Historical skill counts in the dated inventory are not current evidence.
- Activepieces project Ghith's Project (8A2qoCJNq9zlORJZMlrfw) is selected. One project-scoped GitHub connection is ACTIVE (externalId cn2iEAi2bWbLfeOVOu7D8). One flow is ENABLED and published: Raawaa GitHub Workflow Control (oDKKtoRanykDWtyBdkQFt). Its structure is Manual Trigger → GitHub trigger_workflow_dispatch for task-runner.yml on main, with a 30-minute wait and 60-second polling. Latest TESTING run appXqFOAEJKRLnQoA8xJs succeeded and triggered Task Runner run #41749347645. No PRODUCTION Activepieces runs were found.
- Activepieces exposes 20 model entries under provider activepieces. Price, quota, billing, account eligibility, and runtime availability are unverified; no model call was made. These models are not engine provider adapters.
- ChatGPT app permissions: GitHub and Activepieces each show app-specific Allow all actions; global default is Allow low-risk actions. These are ChatGPT UI permission settings, not GitHub API scopes, engine credentials, or runtime adapters.
- ChatGPT automations: four records; only Raawaa Engine Daily Ops is enabled. Its record still reports next_run_time=null and last_run_time=2026-11-02T15:17:12Z. Three unrelated marketing/market/serum automations remain paused. Do not create a duplicate automation or infer recurring scheduler health from this invocation.

### ARTEMIS and device boundary

Google ARTEMIS remains registered but disabled and not runtime verified. No daemon, secure endpoint, ADB-authorized device, or zero-cost model route was verified. Device control is not a prerequisite for cloud-first engine work.

### Next safe action

Keep PR #459 draft until ignored-file integrity is corrected and an actual enforced independent-approval gate exists. Continue using the existing D1 queue, Task Runner, Worker Cron, CI, and Staging verification paths. Do not add a scheduler, queue, provider, integration, paid route, or Production mutation.


### Task Runner schedule-liveness check — 2026-11-04 00:53 UTC

- The current Task Runner workflow still has its existing five-minute schedule at minute offsets 2, 7, 12, 17, 22, 27, 32, 37, 42, 47, 52, and 57 UTC, plus the existing manual workflow_dispatch. Its concurrency group remains d1-task-runner.
- A fresh repository Actions-runs query (first 100 returned runs) showed the latest Task Runner events as workflow_dispatch #41749347645 and push #41743485185; no event=schedule was visible in that result. Primary GitHub schedule liveness is therefore still unverified, not proven disabled. GitHub scheduled events are best-effort.
- The Worker Cron heartbeat is a separate maintenance signal and was current at 00:45:34Z. The GitHub App watchdog is a separate fallback and remains not_ready because GITHUB_APP_WATCHDOG_ENABLED is missing. No schedule, watchdog, Worker variable, secret, or permissions were changed.

## Live revalidation — 2026-11-06 (main `ca61019682a222559a339ad7e4c3c78b13d2a373`)

### Repository, CI, and governance

- Current main SHA: `ca61019682a222559a339ad7e4c3c78b13d2a373`. Exact-main Engine CI [#42257309989](https://github.com/ghith93/raawaa-autonomous-engine/actions/runs/42257309989) passed all five test/Staging/telemetry/readback jobs.
- The exact main Git tree contains 396 entries and 23 workflow YAML files under `.github/workflows/`.
- Current open draft PRs: #574, #536, #523, and #405. Their exact-head CI runs passed historically, but each branch is behind current main; no independent review submissions or review threads were found. No merge was performed.
- Active main ruleset #26464558 requires PRs, strict `test` status, and resolved review threads; it requires zero approving reviews and has a repository-role bypass actor. Do not claim that independent approval is enforced by GitHub.

### Cloudflare, D1, queue, and cost

- Staging migration/Worker/remote D1 lane-isolation/monetary-ledger checks passed in current main CI. Production Control Worker workflow [#42258067719](https://github.com/ghith93/raawaa-autonomous-engine/actions/runs/42258067719) was skipped.
- PR [#581](https://github.com/ghith93/raawaa-autonomous-engine/pull/581) merged as `a15c9bf180559c29e56ff65d6d12e7452f9f9d33`, adding durable lane ownership to SQLite/D1, general-lane backward compatibility, lane-scoped claims/dependency blocking, lane-aware idempotency, and lease-fenced heartbeat/retry/finish. Current main Staging proof in CI #42257309989 passed generic/researcher isolation and wrong-lane lifecycle rejection; no provider or external call occurred and the temporary researcher capability was revoked.
- Dedicated production specialist consumers remain dormant; no per-lane Production secrets or schedule were provisioned. Issue #577 remains open for exact task-ID targeting and complete cancellation/stale-recovery/ambiguous-dispatch lifecycle proof. PR #536 is still an offline prototype.
- Task Runner [#42260926757](https://github.com/ghith93/raawaa-autonomous-engine/actions/runs/42260926757) was a bot-triggered `workflow_dispatch` on current main `ca61019682a222559a339ad7e4c3c78b13d2a373`; health/auth/status returned HTTP 200, Worker Cron heartbeat was `17:15:34Z`, watchdog was ready, and the 17:21:42Z run completed `queue_idle` with 0 tasks processed, 0 pending, and 0 running. This is not a Task Runner `schedule` event or business-task completion.
- Targeted `event=schedule` query found Staging probe [#42211351856](https://github.com/ghith93/raawaa-autonomous-engine/actions/runs/42211351856) successful at 11:35:10Z on earlier main SHA `9f71c3f206ab13f1ee0c81be2813eee582240d14`: `backend=cloudflare_staging`, task `5c904b06-f542-4939-89ed-d6aa8a19a740`, dispatch `0d8662e21f053141ba08d65c8f530356`, `provider_calls=0`, `external_writes=0`, and previous slot absent. It arrived about 7h48m after the nominal 03:47 UTC slot. Runtime-004 scheduled acceptance is now 1/2; one more distinct successful scheduled pass remains required, preferably on current main.
- No `task-runner.yml` `event=schedule` was found in the targeted schedule-event query. The Worker Cron/watchdog `workflow_dispatch` is a separate fallback signal and does not count toward Runtime-004.

- Cloudflare billing inventory [#42257310076](https://github.com/ghith93/raawaa-autonomous-engine/actions/runs/42257310076) read subscriptions successfully (0 total/active/paid) but spend-policy returned HTTP 503 after bounded retries; no external `usage_block` hard stop is verified. No mutation occurred.
- Current engine policy remains safe dry-run with USD 0 configured budgets, paid calls/fallback off, external writes off, `provider_hard_stop_verified=false`, and all provider routes disabled. The $15/month outer ceiling is not spending authorization; no paid inference/API, paid infrastructure, subscription, or automatic paid fallback is permitted under the current owner instruction.

### Connected capabilities and automation

- Activepieces project Ghith's Project has one ACTIVE project-scoped GitHub connection and one ENABLED, published flow: Raawaa GitHub Workflow Control. The flow is Manual Trigger → GitHub `trigger_workflow_dispatch` for `.github/workflows/task-runner.yml` on `main`; it is not a recurring scheduler. No PRODUCTION Activepieces runs were returned. The Activepieces model catalog is visible, but pricing, quotas, account eligibility, and runtime availability are unverified; no model call was made.
- The live ChatGPT automation inventory now returns 8 records: 3 enabled, 4 paused, and 1 completed. Two enabled automations target the engine; a separate enabled cross-project automation includes Raawaa-store and was not touched. See [ChatGPT Automation Inventory](chatgpt-automation-inventory.md) for exact schedules and run evidence.
- Issue #464 remains the durable ChatGPT/Arena handoff. This cycle's plain-text review/handoff comments are [#6691216653](https://github.com/ghith93/raawaa-autonomous-engine/issues/464#issuecomment-6691216653) and [#6691257098](https://github.com/ghith93/raawaa-autonomous-engine/issues/464#issuecomment-6691257098); no Arena-authored reply was present after the prior checkpoint #6685840439. Issue Comment Ingest runs [#42262692176](https://github.com/ghith93/raawaa-autonomous-engine/actions/runs/42262692176) and [#42262989716](https://github.com/ghith93/raawaa-autonomous-engine/actions/runs/42262989716) both returned `action=ignore reason=no_envelope`, so no Production D1 task was enqueued. Arena Agent Mode remains a manual handoff, not an automatically wakeable worker.
- ARTEMIS remains registered but disabled and not runtime verified. No daemon, secure endpoint, ADB-authorized device, or eligible zero-cost model route was verified.

### Next safe actions

- Correct the authenticated GitHub research redirect handling in issue #575 before broadening discovery; keep tests offline with synthetic credentials.
- Keep the research persistence path in issue #576 dormant until its Worker target, redirect-safe readback, idempotent retry, and exact-key verification contract are fixed.
- Preserve the current inference kill switch. Re-verify account tier, exact organization quota, Data Controls/ZDR, and provider-side hard-stop evidence before any future free-only inference.

