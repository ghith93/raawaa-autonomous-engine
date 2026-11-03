# Agent runtime and capability selection — 2026-11-03

## Executive decision

**Do not install or replace the engine's orchestration framework now.** Keep the existing deterministic Python core, D1 task ledger, provider-neutral gateway, policy loader, and bounded GitHub Actions consumer. The current architecture already has the right control-plane boundaries; its main gaps are durable monetary reservations in D1, non-production D1 validation, a real quota-enforced worker loop, and operational observability. Adding a second orchestrator before these are closed would increase failure modes and duplicate state.

The six entries in `config/agents.json` are a declarative role roster, not six running agents. The repository's architecture explicitly says the roster and planner are deterministic, and the README says the engine is not a continuously running autonomous service. GitHub Actions is a bounded ephemeral consumer, not an always-on runtime.

## Verified starting point

- Python core, task store, local SQLite memory, deterministic workflow planner, provider profiles, quota-aware gateway, and Cloudflare Worker/D1 control plane exist.
- D1 is the remote task-state source of truth; local SQLite memory and some usage data are not automatically durable across ephemeral runners.
- The current Task Runner is capped at five tasks / 60 seconds per invocation and two parallel workers.
- All provider routes remain disabled; live inference and external writes remain disabled.
- Issue [#332](https://github.com/ghith93/raawaa-autonomous-engine/issues/332) blocks live inference until monetary reservations and settlements are durable in D1 and concurrency/restart behavior is tested on a separate non-production database.
- Issue [#318](https://github.com/ghith93/raawaa-autonomous-engine/issues/318) records that `main` is unprotected and needs an owner-created repository ruleset.
- The current Worker watchdog was verified ready and dispatched Task Runner run [#41569188010](https://github.com/ghith93/raawaa-autonomous-engine/actions/runs/41569188010); its bounded run was idle (`tasks_processed=0`). This proves dispatch and an idle run, not execution of a business task.

## Framework comparison for this repository

| Candidate | Documented strengths | Cost/operational fit | Decision |
|---|---|---|---|
| PydanticAI | Typed inputs/outputs, model/tool abstractions, deferred tool approval, and integrations for durable engines such as Temporal, DBOS, Prefect and Restate | Python-native and modular, but durable execution still needs a separate runtime/service; provider calls still need verified budget and credentials | **Best future typed-agent adapter candidate**; do not install until the D1 budget ledger and opt-in contract exist |
| LangGraph | Low-level stateful graphs, persistence/checkpointers, long-running execution, interrupts and human-in-the-loop resume | Strong when workflows genuinely need branching and durable checkpoints; adds a second orchestration model if used beside the existing task planner | **Defer**; adopt only if a measured workflow needs graph state that the deterministic planner cannot express cleanly |
| Microsoft Agent Framework | Python/.NET agent and workflow orchestration, sequential/concurrent/handoff patterns, checkpointing, streaming, human-in-the-loop, observability | Already isolated behind optional pinned evaluation dependencies in this repository; broader package/runtime surface than the current core | **Keep isolated evaluation**; do not promote to production without a benchmark against the current runner and non-production durability tests |
| OpenAI Agents SDK | Lightweight agents, handoffs, guardrails, function tools, MCP tools, sessions, sandbox agents, human-in-the-loop and tracing | Useful if the runtime is intentionally built around these primitives; tracing and model calls require a separate data/cost policy review | **Candidate for a narrow specialist adapter**, not a replacement for D1 scheduling or policy enforcement |
| Agno / AgentOS | Hosted/self-hosted runtime with API/MCP, auth, role access, persistent state, tracing, durable queues, scheduler and human approvals | A broad control plane and service to operate; its documented examples use FastAPI and database infrastructure, which overlaps with current Worker/D1 control plane | **Do not adopt now**; high overlap and likely hosting/maintenance cost |
| goose | Open-source general-purpose CLI/desktop/API agent, MCP extensions, recipes, skills, subagents and security controls | Useful as an operator-side tool or cloud-hosted helper; not a replacement for the engine's durable task ledger; still requires a model route and execution host | **Keep as a research candidate** for controlled cloud coding/research jobs |
| OpenHands | Agent Server/SDK, workspaces, multi-agent sessions, Automation Server and cloud-VM/self-host deployment options | Closest fit for real remote coding execution from a phone/tablet, but requires a managed runtime/sandbox and a model; no zero-cost always-on hosting has been verified | **Highest-value remote coding pilot candidate**, conditional on verified free compute and model access |
| Mem0 | Self-hostable memory library/server with configurable LLM, embedder, vector store and reranker | The default setup uses an LLM and embeddings; a self-hosted vector stack adds services and resource use. Current structured memory is simpler | **Defer**; first make existing project/task memory durable and source-attributed in D1 |

### Architectural rule

Do not let a framework become a second source of truth. D1 remains the canonical task and budget ledger. Any future agent runtime must consume tasks through the existing claim/heartbeat/finish contract, call the mandatory operating-policy loader before model/tool execution, use the existing provider gateway and quota reservation interfaces, and emit auditable structured results. It must not create its own competing scheduler, queue, budget ledger, or unrestricted tool surface.

## Cloudflare execution constraints

Cloudflare's current D1 limits documentation lists a 500 MB maximum database size on Free, 10 databases per Free account, 50 read subrequests per Worker invocation, six simultaneous D1 connections per Worker invocation, and a 30-second SQL query duration limit. These are hard operational boundaries, not permission to incur paid overages.

A separate non-production D1 database is required before validating cross-runner monetary concurrency, crash/restart recovery, duplicate settlement, and ambiguous provider outcomes. The recent billing inventory verified the account subscriptions endpoint but the spend-policy endpoint returned HTTP 404; therefore an account-level `usage_block` hard stop is **not verified**. Keep paid overages disabled and do not enable paid fallback.

Cloudflare Access is not enabled for this account. The Worker currently uses its own authenticated API token; do not add Access or weaken the existing API authentication merely to make a dashboard look more complete.

## Capability and connector boundary

The current ChatGPT session exposes GitHub, web research, Firecrawl, TinyFish, Context7, Notion, Figma, Supabase, Activepieces and other app tools. These are assistant-side capabilities; they are **not automatically available to Python workers in GitHub Actions**. The engine needs explicit, narrowly scoped adapters before its agents can use them.

Current inventory evidence:
- Activepieces returned zero flows and zero connections.
- No Cloudflare MCP connector was present in the available tool catalog.
- TinyFish profiles did not show a Cloudflare sign-in; do not assume Cloudflare dashboard access through the browser.
- The ChatGPT automation `Raawaa Engine Daily Ops` is enabled, but its latest recorded run failed with a generic scheduled-task error and no next-run time was reported. This is separate from the GitHub Actions Task Runner and must not be counted as healthy execution evidence.

Do not bulk-install plugins or copy every available skill into the repository. Load skills only for the task being executed; treat skill instructions as workflow guidance, not as executable agent capabilities.

## Prioritized implementation sequence

1. **Protect `main`** — owner creates a repository ruleset requiring PRs and exact-head Engine CI, blocking force-push and branch deletion, and restricting bypass. The current connector cannot create this administrative ruleset.
2. **Create/verify a dedicated non-production D1 database** within the account's free-tier limits. Do not run the proposed monetary migration against production during initial validation.
3. **Implement issue #332** — additive D1 monetary reservation/settlement ledger, idempotent settlement, ambiguous-reservation retention, cross-provider/task/day/month caps, and a Python D1 adapter that fails closed with no SQLite fallback in cloud execution.
4. **Prove the ledger** — concurrent reservation, crash/restart, duplicate settlement, provider timeout, over-cap and reconciliation tests against non-production D1.
5. **Only then pilot one real agent task type** — public-only `research_brief`, strict JSON schema, one specialist at a time, verified-free model only, per-task and daily quota reservations, no external writes, explicit kill switch, reviewer validation and audit record.
6. **Improve observability** — populate scan heartbeat, source freshness, CI health, kill-switch mirror and task outcome signals; distinguish missing data from healthy zero values.
7. **Evaluate remote coding runtime** — test OpenHands Agent Server in a disposable/free environment against a fixed issue benchmark. Keep it out of production until isolation, credentials, costs, and teardown are verified.

## Explicit non-goals

- No framework migration or broad dependency installation.
- No paid API calls, subscriptions, credits, upgrades, or paid fallback.
- No production D1 monetary migration as part of initial testing.
- No wallet signing, trading, exchange orders, withdrawals, customer messaging, publishing, or storefront modification.
- No AndroidHarness or device-dependent runtime; the supported path remains cloud-first.

## Primary references

- [PydanticAI durable execution](https://ai.pydantic.dev/durable_execution/overview/) and [deferred tools / approval](https://ai.pydantic.dev/deferred-tools/)
- [LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence) and [interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)
- [Microsoft Agent Framework repository](https://github.com/microsoft/agent-framework) and [official documentation](https://learn.microsoft.com/en-us/agent-framework/)
- [OpenAI Agents SDK](https://openai.github.io/openai-agents-python/) and [MCP tools](https://openai.github.io/openai-agents-python/mcp/)
- [Agno AgentOS](https://docs.agno.com/agent-os/overview)
- [goose extensions](https://github.com/aaif-goose/goose/blob/main/documentation/docs/getting-started/using-extensions.md)
- [OpenHands architecture](https://github.com/OpenHands/openhands)
- [Mem0 open-source overview](https://github.com/mem0ai/mem0/blob/main/docs/open-source/overview.mdx)
- [Temporal Python SDK](https://docs.temporal.io/develop/python)
- [Cloudflare D1 limits](https://developers.cloudflare.com/d1/platform/limits/)
- [Cloudflare database connection guidance](https://developers.cloudflare.com/workers/databases/connecting-to-databases/)
