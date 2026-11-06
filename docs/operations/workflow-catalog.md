# Raawaa Autonomous Engine — GitHub Actions Workflow Catalog

**Source:** Exact `.github/workflows/` tree on `main` at `ca61019682a222559a339ad7e4c3c78b13d2a373`, checked 2026-11-06. This catalog lists the 23 repository workflow YAML files; generated platform workflows are excluded. Presence in the tree is not proof that a workflow has run or is safe to dispatch.

## Catalog

| Workflow | File | Initial classification | Use / caution |
|---|---|---|---|
| Microsoft Agent Framework Isolated Evaluation | `agent-framework-eval.yml` | PR-scoped evaluation | Installs pinned optional dependencies and runs deterministic evaluation; not a production runtime. |
| Engine CI | `ci.yml` | Test/CI + Staging verification | PRs run tests; main pushes may apply Staging migrations and deploy the isolated Staging Worker. Production deploy is separate. |
| Cloudflare Access Audit | `cloudflare-access-audit.yml` | Read-only account audit | Uses a dedicated read-only Access audit token. |
| Cloudflare Access Inventory | `cloudflare-access-inventory.yml` | Read-only account inventory | Access may not be enabled on the account. |
| Cloudflare Billing Spend-Policy Inventory | `cloudflare-billing-inventory.yml` | Read-only billing inventory | Bounded retries; current spend-policy endpoint returned HTTP 503, so no external `usage_block` is verified. |
| Cloudflare Worker Inventory | `cloudflare-worker-inventory.yml` | Read-only Worker inventory | Inspects Worker scripts, routes, and health; no configuration change. |
| Cloudflare Workers AI Read-only Catalog Check | `cloudflare-workers-ai-inventory.yml` | Read-only model catalog | Does not perform inference or verify account billing eligibility. |
| Coding Agent CLI Installation Smoke | `coding-agent-cli-smoke.yml` | Tool installation/evaluation | Installs optional CLIs; inspect downloads and permissions before manual dispatch. |
| D1 Connectivity Check | `d1-healthcheck.yml` | Read-only health check | Connectivity only; not migration or data-write proof. |
| Apply D1 Migrations | `d1-migrate.yml` | **Database write** | Inspect exact target and environment first. Use Staging only for new migrations; never infer target from workflow name. |
| D1 Migration Inventory (Read Only) | `d1-migration-inventory.yml` | Read-only schema inventory | Does not apply migrations. |
| D1 Staging Inventory (Read Only) | `d1-staging-inventory.yml` | Read-only Staging inventory | Verifies isolated Staging target/schema. |
| Deploy Control Worker | `deploy-worker.yml` | **Production deployment** | Main-push marker gated; requires separate owner approval for the exact deployment. |
| Groq API Connection Check | `groq-connection-check.yml` | Read-only provider check | Authentication/model-catalog check only; does not prove current Free-tier billing, quotas, privacy settings, or inference success. |
| One-shot Groq Free inference Staging smoke | `groq-free-inference-staging-smoke.yml` | **One-shot provider inference in Staging** | Keep disabled until current account tier, exact quota, Data Controls, provider hard stop, and explicit authorization are verified. |
| Issue Comment Ingest | `issue-comment-ingest.yml` | Event-triggered task enqueue | Currently enabled for the allowlisted actor/issue. Plain comments are ignored; a valid task envelope on #464 can write to Production D1. This cycle's comment #6691216653 was ignored (`no_envelope`) in run #42262692176. Do not send an envelope without action-specific Production-write approval. |
| Engine Maintenance | `maintenance.yml` | Scheduled read-only audit/test | Runs configuration audit, tests, and read-only development ranking; no merge/deploy/provider activation. |
| Scheduled Open-Source Research Discovery | `research-discovery.yml` | Scheduled public research + optional persistence | Public metadata discovery and artifacts; optional D1 persistence/telemetry writes are separately gated. No model inference. |
| Sandbox Adversarial Proof | `sandbox-adversarial.yml` | Manual security evaluation | Downloads gVisor and a digest-pinned image; run only for an explicitly scoped sandbox proof. |
| Report which secrets are configured | `secret-readiness.yml` | Read-only secret-presence check | Reports booleans/names only; never reads or prints secret values. |
| Staging P1 end-to-end task proof (manual) | `staging-p1-task-proof.yml` | **Staging D1 write** | Enqueues/claims one harmless Staging task and revokes its temporary token; not schedule acceptance. |
| Staging scheduled supervised process probe | `staging-supervised-process-probe.yml` | **Scheduled Staging-only task** | Daily at 03:47 UTC; no manual dispatch. One successful `event=schedule` run #42211351856 on 2026-11-06 used Cloudflare Staging with zero provider/external calls; Runtime-004 is 1/2. It arrived about 7h48m late; one more distinct scheduled pass is required. |
| Scheduled D1 Task Runner | `task-runner.yml` | **Bounded Production queue consumer** | Five-minute schedule plus workflow_dispatch; can claim/settle Production tasks. Do not dispatch without explicit approval for Production task operations. |

## Execution decision rules

- **Read-only inventory:** preferred first when discovering an account, database, Worker, provider or connection.
- **Tests/CI:** safe default for code validation; confirm exact commit SHA.
- **Bounded queue consumer:** verify queue depth, task type, policy, budgets and kill switch before manual dispatch.
- **Provider inference:** only one-shot, public-only, free route with provider-specific enforced hard limit, no paid fallback, one attempt, and auditable result/usage.
- **Production Worker deployment:** only after exact-head CI and deployment-target validation; preserve dashboard-managed vars; fail closed if runtime readiness regresses.
- **Production D1 migrations, external deployments, financial actions, customer messages, and destructive operations:** separate action-specific authorization required.
- Never dispatch a workflow by guessed name or ID. Resolve the exact workflow selector entry, inspect the YAML, then invoke only the intended action.

## Workflow discovery method

The current Activepieces flow `Raawaa GitHub Workflow Control` is Manual Trigger → GitHub `trigger_workflow_dispatch` for `.github/workflows/task-runner.yml` on `main`. It is a narrow manual helper, not a universal selector or recurring scheduler. Use the checked-in workflow file and its exact trigger/guards before any dispatch. The current repository tree is the authoritative workflow inventory.


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

## Live workflow-tree and dispatch reconciliation — 2026-11-06

- Current main `ca61019682a222559a339ad7e4c3c78b13d2a373` contains exactly 23 files under `.github/workflows/`; the table above matches the live tree. The obsolete `openrouter-nemotron-free-smoke.yml` entry is removed; the actual tree includes `issue-comment-ingest.yml`, `sandbox-adversarial.yml`, `secret-readiness.yml`, and `staging-p1-task-proof.yml`.
- The current Activepieces flow is a manual Task Runner dispatch helper. It is not a recurring trigger and has no PRODUCTION runs in the current inventory.
- Task Runner run [#42260926757](https://github.com/ghith93/raawaa-autonomous-engine/actions/runs/42260926757) was created as `workflow_dispatch` by `raawaa-engine[bot]` on current main at 17:21:42Z, after the Worker Cron heartbeat at 17:15:34Z and watchdog dispatch intent at 17:21:40Z. It completed `queue_idle` with 0 tasks processed, 0 pending, and 0 running; watchdog was ready. This is an automated idle poll, not a Task Runner `schedule` event or business-task completion.
- A targeted `event=schedule` query found Staging probe [#42211351856](https://github.com/ghith93/raawaa-autonomous-engine/actions/runs/42211351856) successful at 11:35:10Z on earlier main SHA `9f71c3f206ab13f1ee0c81be2813eee582240d14`, using `backend=cloudflare_staging`, task `5c904b06-f542-4939-89ed-d6aa8a19a740`, dispatch `0d8662e21f053141ba08d65c8f530356`, `provider_calls=0`, and `external_writes=0`. It arrived about 7h48m after the nominal 03:47 UTC slot; Runtime-004 acceptance is 1/2 and requires one more distinct successful schedule run.
- No `task-runner.yml` `event=schedule` was found in the targeted schedule-event query. Do not count the watchdog's `workflow_dispatch` as a Staging supervised-probe pass.

- Production Control Worker run [#42258067719](https://github.com/ghith93/raawaa-autonomous-engine/actions/runs/42258067719) was skipped on current main. No Production deployment was performed in this cycle.

