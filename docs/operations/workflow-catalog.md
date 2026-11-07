# Raawaa Autonomous Engine — GitHub Actions Workflow Catalog

**Manual-dispatch list source:** Activepieces GitHub `trigger_workflow_dispatch` workflow selector for repository `ghith93/raawaa-autonomous-engine`, checked 2026-11-03 21:50 UTC. The catalog also records scheduled-only repository workflows that intentionally do not appear in that selector. Listing a workflow is not proof that it has run or is safe to run.

## Catalog

| Workflow | File | Initial classification | Use / caution |
|---|---|---|---|
| Microsoft Agent Framework Isolated Evaluation | `agent-framework-eval.yml` | Evaluation | Inspect full YAML and cost/network scope before running. |
| Engine CI | `ci.yml` | Test/CI | Required status context is `test`; PR and main CI evidence. |
| Cloudflare Access Audit | `cloudflare-access-audit.yml` | Read-only account audit | Uses dedicated read-only Access audit token. |
| Cloudflare Access Inventory | `cloudflare-access-inventory.yml` | Read-only account inventory | Access may not be enabled on the account. |
| Cloudflare Billing Spend-Policy Inventory | `cloudflare-billing-inventory.yml` | Read-only billing inventory | No spend policy hard stop should be inferred from a 404. |
| Cloudflare Worker Inventory | `cloudflare-worker-inventory.yml` | Read-only Worker inventory | Check Worker scripts, routes and health. |
| Cloudflare Workers AI Read-only Catalog Check | `cloudflare-workers-ai-inventory.yml` | Read-only catalog | Does not perform inference or verify model billing eligibility. |
| Coding Agent CLI Installation Smoke | `coding-agent-cli-smoke.yml` | Installation/evaluation | Installs CLI tooling; inspect downloads and permissions before use. |
| D1 Connectivity Check | `d1-healthcheck.yml` | Read-only health check | Connectivity only; not migration or data-write proof. |
| Apply D1 Migrations | `d1-migrate.yml` | **Database write** | Inspect target and environment before use. Use Staging only for new migrations. Never assume the target from the workflow name. |
| D1 Migration Inventory (Read Only) | `d1-migration-inventory.yml` | Read-only schema inventory | Does not apply migrations. |
| D1 Staging Inventory (Read Only) | `d1-staging-inventory.yml` | Read-only staging inventory | Verifies isolated Staging target/schema. |
| Deploy Control Worker | `deploy-worker.yml` | **Production deployment** | No manual dispatch; authorized only by a `main` push whose commit message contains `[deploy-control-worker]`. After PR #432, preserve dashboard vars and fail if watchdog is not ready. |
| Groq API Connection Check | `groq-connection-check.yml` | Read-only provider check | Authentication/catalog only; does not prove free inference success. |
| One-shot Groq Free inference Staging smoke | `groq-free-inference-staging-smoke.yml` | **One-shot provider inference in Staging** | Uses isolated Staging and ephemeral bearer credentials; intended to revoke them. Run only after inspecting exact script and quota evidence. |
| Engine Maintenance | `maintenance.yml` | Maintenance | Inspect exact actions/targets before running; may mutate task/lease state. |
| One-shot OpenRouter Nemotron Free smoke | `openrouter-nemotron-free-smoke.yml` | **One-shot provider inference** | Verify account eligibility, hard quota, cost, paid fallback disabled and exact model before use. |
| Scheduled Open-Source Research Discovery | `research-discovery.yml` | Scheduled public research | Existing research scheduler; do not create a duplicate. |
| Scheduled D1 Task Runner | `task-runner.yml` | Bounded queue consumer | Existing task runner; production control Worker only. Live inference remains disabled by default; inspect the queue and policy before dispatch. |
| Staging scheduled supervised process probe | `staging-supervised-process-probe.yml` | **Scheduled Staging-only read-only process task** | Daily at 03:47 UTC; no manual dispatch; enqueues and claims only its own `supervised_probe` ID, uses a temporary Staging token, and must never target Production. Schedule proof remains pending until a run with event `schedule` succeeds. |
| Dependabot Updates | `dynamic/dependabot/dependabot-updates` | Dependency maintenance | Dynamic workflow ID; inspect generated PRs and exact changes before merge. |

## Execution decision rules

- **Read-only inventory:** preferred first when discovering an account, database, Worker, provider or connection.
- **Tests/CI:** safe default for code validation; confirm exact commit SHA.
- **Bounded queue consumer:** verify queue depth, task type, policy, budgets and kill switch before manual dispatch.
- **Provider inference:** only one-shot, public-only, free route with provider-specific enforced hard limit, no paid fallback, one attempt, and auditable result/usage.
- **Production Worker deployment:** only after exact-head CI and deployment-target validation; preserve dashboard-managed vars; fail closed if runtime readiness regresses.
- **Production D1 migrations, external deployments, financial actions, customer messages, and destructive operations:** separate action-specific authorization required.
- Never dispatch a workflow by guessed name or ID. Resolve the exact workflow selector entry, inspect the YAML, then invoke only the intended action.

## Workflow discovery method

Use the connected Activepieces GitHub action `trigger_workflow_dispatch` with repository `ghith93/raawaa-autonomous-engine`; resolve the `workflow` property after selecting the repository. This returned the authoritative 25-entry list above. The existing Activepieces flow `Raawaa GitHub Workflow Control` is preconfigured for the standard Task Runner workflow only; do not mistake it for a universal workflow selector.


## Live workflow-tree reconciliation — 2026-11-04 00:51 UTC

- The exact main tree at e6761d09de884d34bdc0ef7d01d7b7a8f1e1682e contains 23 workflow YAML files under .github/workflows. The current list includes the existing CI, Task Runner, research discovery, Cloudflare inventory/audit/billing, D1 inventory/migration, Staging deployment, Production Worker deployment, Groq connection and Staging smoke, and read-only market-data workflows. Reuse these exact entry points; do not add another scheduler or queue consumer.
- The earlier catalog entry for openrouter-nemotron-free-smoke.yml is not present in the current main workflow tree. Treat that entry as stale/historical and do not dispatch it unless a current workflow file and selector entry are independently verified. The dynamic Dependabot workflow is platform-generated and is not a file in this repository tree.
- Main CI run #41747902547 passed on exact main SHA e6761d09de884d34bdc0ef7d01d7b7a8f1e1682e, including all Staging migration, isolated Worker, monetary-ledger, telemetry, and authenticated mobile-status jobs.
- The existing Task Runner workflow was dispatched through the existing Activepieces flow. TESTING flow run appXqFOAEJKRLnQoA8xJs succeeded and triggered GitHub run #41749347645. The bounded run completed with queue_idle and processed 0 tasks; this is not proof of live inference or business-task completion.
- Production Control Worker workflow run #41748314581 was skipped. Production Worker and D1 remain unchanged.
- The active main-protection ruleset requires PRs, strict test status, and resolved review threads, but currently requires zero approving reviews. Do not treat an independent approval as an enforced repository rule.


### Task Runner schedule-liveness checkpoint — 2026-11-04 00:53 UTC

- The existing task-runner.yml retains its five-minute cron offsets 2, 7, 12, 17, 22, 27, 32, 37, 42, 47, 52, and 57 UTC, plus workflow_dispatch; concurrency remains grouped as d1-task-runner.
- The latest Task Runner events visible in the first 100 repository workflow runs were workflow_dispatch #41749347645 and push #41743485185. No event=schedule appeared in that result. Treat primary GitHub cron liveness as unverified; do not create another scheduler or infer schedule health from the manual dispatch.
- The separate Worker Cron heartbeat was recorded at 00:45:34Z. The GitHub App watchdog remains not_ready because GITHUB_APP_WATCHDOG_ENABLED is missing.


## Live workflow reconciliation — 2026-11-07 (post-PR #614)

- PR #614 is merged at main commit `827362368113499fdd24610e3701c88d04e139ad`. Its exact-head CI passed, but all remote Staging jobs were skipped. The updated `groq-20b-staging-dispatch.yml` workflow is an audit/preflight path; it must not be described as a Groq inference smoke or as proof of idempotent task execution.
- The workflow now uses immutable action references, validates dispatch inputs without interpolating untrusted values into shell source, checks the exact run context, and emits only allowlisted reconciliation metadata. The run-scoped value is a correlation identifier, not a durable deduplication ledger.
- Do not dispatch this audit workflow as a substitute for the separate one-shot Staging inference workflow. Provider inference remains disabled by default and the USD 0 ceiling/paid-fallback prohibition remain authoritative.
- Runtime-004 remains 1/2; only a second distinct successful scheduled Staging probe can close that acceptance item.

## Follow-up after PR #643 and #644 — 2026-11-07

- PR #643 merged: generic task claims now enforce attempts < max_attempts in both selection and conditional update; an at-cap pending task is not falsely claimed or audited.
- PR #644 merged: duplicate normalized research source keys are rejected before any D1 batch is constructed, preventing accepted_count from overstating unique persisted candidates.
- PR #641 remains the base for durable research persistence; this follow-up automates its use only after billing, migration and Worker schema checks, with per-run ephemeral Staging credentials.
- The current Task Runner watchdog status is ready; latest inspected automatic dispatch #42318764304 succeeded with an idle queue (0 pending / 0 running). Do not confuse a successful idle poll with actual task processing.
