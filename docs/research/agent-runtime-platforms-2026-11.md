# Agent Runtime and Memory Projects — Evaluation for Raawaa Engine

**Research date:** 2026-11-02
**Decision:** Keep the existing engine control plane. Prototype Cloudflare Workflows next. Do not install another full agent framework or vector database until a measured gap justifies it.

## Executive decision

Raawaa has a policy loader, task model, quota controls, D1-backed state, a Microsoft Agent Framework checkpoint adapter, GitHub Actions, and no required device worker. Adding another full orchestration framework now would duplicate scheduling, persistence, memory and approval logic. Cloudflare Workflows has a local runtime prototype, but production adoption still requires separate D1 durability and recovery validation. The current Cloudflare Agents SDK and Python Workers are useful new candidates, but neither is a drop-in replacement for the existing Python consumer: Agents SDK requires a Worker-native coordinator, while Python Workers are open beta and the Free CPU/threading constraints need an isolated compatibility benchmark.

## Candidate comparison

| Project | Strength | Fit | Decision |
|---|---|---|---|
| Cloudflare Workflows | Durable multi-step Workers, retries, persisted state, pauses and event-driven resume | High: existing Cloudflare Worker/D1 environment; available on Free and Paid plans | **Prototype next** with deterministic fake tasks |
| Cloudflare Agents SDK | Durable Object agent identities, schedules, fibers, recovery and sub-agent lifecycle | High for a future Worker-native coordinator; low as a direct host for current Python handlers | Do not adopt now; isolated fake-task spike only if a coordinator gap is measured |
| Cloudflare Python Workers | Python/Pyodide runtime with D1, Cron and other Workers bindings | Medium for thin control-plane tasks; weak for the current batch runner because Free CPU is 10 ms and `threading`/`multiprocessing` are non-functional | Compatibility benchmark only; do not port the runner yet |
| Pydantic AI | Typed agents/tools, MCP client, StepPersistence, optional durable engines | Medium: useful for a small agent-runtime comparison; true crash recovery still needs an execution engine | Isolated benchmark only |
| LangGraph | Stateful graph orchestration, checkpoints, pending writes, thread/checkpoint IDs | Medium: strong checkpoint reference, but duplicates the current MAF adapter and task graph | Borrow tests and semantics; do not add to production |
| Microsoft Agent Framework | Current isolated workflow/checkpoint prototype | High short-term: already integrated and verified through adapter tests and real workflow resume | Continue restart/authentication tests |
| Agno | Agent teams, task modes, shared state, memory, guardrails, metrics and human-in-the-loop | Medium: broad alternative, but a second runtime | Revisit only if current scheduler lacks a measured capability |
| Goose | CLI/API agent, MCP extensions, recipes, subagents and permission controls | Not required: device execution is outside the critical path | Study recipes/MCP patterns; do not install |
| OpenHands | Agent Server, Automation service, remote sandboxes and browser/files/terminal | Medium for a secured cloud VM; poor fit for Android tablet | Optional future cloud coding worker only |
| Mem0 | Extracted memory facts, SDK or self-hosted server, vector/history stores | Low now: adds model/embedder and vector-store dependencies | Do not add until memory retrieval quality is measured |
| Letta | Stateful agents with git-backed memory blocks and background consolidation | Medium conceptually, but duplicates current project memory and requires a server/model route | Borrow core/on-demand memory patterns; no deployment |
| Graphiti / Zep | Temporal knowledge graph with validity windows and provenance | Low now: requires graph database, LLM/embedder and ingestion operations | Defer until temporal entity relationships become a real need |
| Qdrant | Dense/hybrid vector retrieval; Qdrant Edge offers embedded/offline retrieval | Low now: no demonstrated semantic-retrieval bottleneck; server adds ports and storage operations | Defer; consider Edge only after a retrieval benchmark |

## 1. Cloudflare Workflows — recommended next spike

Cloudflare Workflows is available on Free and Paid plans and supports multi-step execution, automatic retries, persisted state, waits for external events and human approvals. The current Free plan includes 100,000 requests/day shared with Workers, 3,000 steps/day, 1 GB-month of persisted state, 10 ms active CPU per invocation, and a 1,024-step ceiling per Workflow. Step and storage billing began 2026-08-10, so Free availability is bounded rather than unmetered. See the [official pricing](https://developers.cloudflare.com/workflows/reference/pricing/) and [limits](https://developers.cloudflare.com/workflows/reference/limits/) pages. Keep the prototype isolated; no production activation or paid-plan assumption.

Cloudflare Queues are useful for dispatch and backpressure, but not as the durable source of truth: the Free plan has 24-hour message retention, messages are limited to 128 KB, and each consumer invocation has a 15-minute wall-time limit. Store durable task state in the approved store or Workflow state; queue messages should contain small task IDs or pointers.

Isolated prototype:
1. Add a separate Workflow class on a feature branch; do not change the production Worker entrypoint or D1 schema.
2. Use deterministic fake steps only: claim → research stub → review stub → checkpoint → wait/resume → complete.
3. Inject failures between steps and prove completed steps are not repeated.
4. Test duplicate events, idempotency keys, cancellation, timeouts, replay and pause/resume.
5. Run local Wrangler tests and CI only. No provider keys, paid calls or deployment.
6. Compare reliability and complexity with the current scheduled GitHub Actions Task Runner and checkpoint adapter.
7. Adopt only if it demonstrably improves durability without weakening policy/quota controls.

Do not place raw prompts, large state blobs or secrets in queue messages. Keep durable task metadata in the approved state store.


## 2. Cloudflare Agents SDK and Durable Objects — new runtime candidate (2026-11-03)

Cloudflare's Agents SDK now documents a durable, Worker-native agent lifecycle built on Durable Objects. Each named agent has persistent SQLite-backed state, scheduled wake-ups, WebSocket/RPC routing and hibernation while idle. The SDK exposes two execution primitives that are relevant to this project:

- `runFiber(name, fn)` registers work durably, holds the object alive while it runs, and allows explicit JSON checkpoints through `ctx.stash()`. If the object is evicted or restarted, `onFiberRecovered()` receives the last checkpoint and the application must decide how to resume safely.
- `startFiber(name, fn, options)` adds durable acceptance, a stable idempotency key, retained status, inspection/cancellation and deduplication for retried webhook deliveries. It is a better fit when an HTTP/webhook caller must receive an accepted receipt before background work finishes.
- Agents can invoke Workflows for heavyweight multi-step pipelines; the two primitives are complementary rather than mutually exclusive.

### Fit against the existing engine

| Dimension | Agents SDK / Durable Objects | Existing engine / Workflows |
|---|---|---|
| Primary role | Long-lived, addressable agent identity and event-driven lifecycle | Existing D1 task control plane plus bounded Python consumer; Workflows for durable multi-step jobs |
| Durable state | Per-object SQLite and explicit fiber checkpoints | D1 is the canonical shared task ledger; Workflow persists completed step results |
| Recovery | Application implements `onFiberRecovered()` and checkpoint-aware replay | Workflow retries failed steps; the current Python runner uses D1 leases, fencing and bounded retry |
| Idempotency | `startFiber()` accepts a stable idempotency key | Existing task/operation stores and webhook receipt table use explicit idempotency contracts |
| Main integration cost | New Durable Object binding/class/migration and a Worker-native TypeScript execution surface | Existing Worker/D1 and Python runtime are already present |
| Fit for current Python handlers | Low without porting handlers or building a carefully bounded bridge | High for the existing runner; Workflows can host Worker-native steps |

The Agents SDK is a credible option for a future Worker-native coordinator, webhook ingress, durable reminders and status notifications. It does **not** execute the current Python task handlers inside a Durable Object. Porting those handlers or adding a remote bridge would create a new execution boundary, and a second consumer would risk competing with the existing D1 queue runner. The SDK therefore does not solve the current five-minute GitHub schedule reliability gap by itself.

### Cost and safety boundary

Durable Objects have published Free-plan allowances, including 100,000 requests/day, 13,000 GB-seconds/day, 50 million SQL rows read/day, 100,000 SQL rows written/day and 5 GB of stored data. These are bounded account-level allowances, not an unlimited always-on guarantee. Any prototype must meter requests, storage, alarms and writes, stop at its configured budget, and never assume that exceeding a Free allowance can fall back to paid usage. See the [Cloudflare plan limits](https://www.cloudflare.com/plans/) and [Workers limits](https://developers.cloudflare.com/workers/platform/limits/).

A fiber checkpoint is not a blanket exactly-once guarantee for arbitrary external effects. Recovery code must use stable operation keys, inspect the last checkpoint, and reconcile ambiguous downstream outcomes before replay. Keep secrets and private task payloads out of metadata and logs. Retain D1 as the canonical task ledger if an isolated prototype is ever run.

### Decision

**Do not adopt or deploy the Agents SDK now.** Keep the current Python-first control plane and the isolated Workflows prototype. First resolve/measure the existing Task Runner schedule gap and complete non-production D1 recovery/concurrency validation. If a Worker-native control agent is still justified, run a separate local/CI-only spike with fake tasks that tests durable webhook acceptance, duplicate idempotency keys, forced eviction, checkpoint recovery, cancellation and bounded alarms. It must not add a second D1 queue consumer, call a model, mutate production D1, configure Telegram secrets, register a webhook or deploy a Worker. Compare the spike against Workflows and the current MAF checkpoint adapter before making an adoption decision.


## 3. Cloudflare Python Workers — possible Python-native execution path

Cloudflare Python Workers provide a first-class Python runtime through Pyodide/WebAssembly, with access to D1, Durable Objects, Cron Triggers, secrets and other Worker bindings through the Python-to-JavaScript FFI. Cloudflare also publishes examples for Python Cron handlers and querying D1 from Python. This is materially more relevant to the existing engine than adding a generic coding-agent framework: it could eventually let selected Python control-plane handlers run close to the existing D1 ledger without relying on a GitHub-hosted Python process.

However, the current compatibility boundary is significant:

- Python Workers remain in **open beta** and require the `python_workers` compatibility flag.
- The Free Workers plan currently allows 10 ms CPU per invocation, 128 MB memory, 100,000 requests/day and five Cron Triggers per account. A 5-task / 60-second batch is not a realistic assumption under the 10 ms CPU ceiling; asynchronous waiting does not make CPU-heavy Python work free.
- Pyodide's `threading` and `multiprocessing` modules are not functional. The existing engine's `LeaseHeartbeat` uses a background thread, so the current worker path is not directly portable without a deliberate async lease-renewal redesign.
- The Worker filesystem is ephemeral and isolate-local. The current local SQLite task/memory assumptions cannot be carried over as persistent storage; D1 must remain authoritative for shared task state, with explicit bindings and async access.
- Package support is limited to pure-Python or available Pyodide/PyEmscripten wheels. Each dependency used by the engine must be checked rather than assuming ordinary CPython compatibility.

### Decision

**Do not replace the GitHub Actions consumer or port the full engine now.** First build a no-provider compatibility benchmark in an isolated branch: import the actual engine modules under Pyodide, audit unsupported imports, measure cold-start and CPU use, test D1 binding operations, and prototype async lease renewal without threads. The benchmark must use local simulated D1/Workerd only, never production D1. If the real dry-run handler cannot fit the Free CPU/memory limits or requires unsupported packages, stop rather than silently move to a paid Workers plan. A thin Worker-native control/status adapter may still be useful even if the Python task executor remains on GitHub Actions.

This is distinct from the Agents SDK: Python Workers answer **where existing Python code can execute**, while the Agents SDK supplies a Durable Object lifecycle and agent identity. Neither removes the need for an idempotent D1 task contract or proves reliable periodic execution by itself.

## 4. Fresh GitHub discovery candidates — triaged 2026-11-03

The discovery workflow [run #41551442181](https://github.com/ghith93/raawaa-autonomous-engine/actions/runs/41551442181) completed a discovery-only pass: six GitHub repository queries and one Crossref request, yielding 26 GitHub candidates and 10 academic records. The triage artifact made zero model calls, executed no candidate code, cloned/installed nothing, and set `automatic_adoption=false`. Its priority scores are search/metadata triage signals, not quality, security or production-readiness ratings.

Four candidates with direct relevance were inspected at repository-metadata and README level:

| Candidate | Verified signal | Potential value | Fit decision |
|---|---|---|---|
| [Omnigent](https://github.com/omnigent-ai/omnigent) | Apache-2.0 metadata; Python; actively updated; README labels project alpha; requires Python 3.12+ and Node.js 22+ for the full toolchain | Phone/browser control surface, mixed coding harnesses, policy enforcement, optional cloud sandboxes | **Monitor; do not install.** It is a meta-harness, not a lightweight library. Most hosted sandbox integrations require separate accounts/credentials and may incur cost; running it requires a persistent host and a second control plane. It could be reconsidered only if a genuinely free, approved host and a measured mobile-supervision gap are established. |
| [Microsoft Agent Framework](https://github.com/microsoft/agent-framework) | MIT; Python/.NET; active; graph workflows, checkpoints, middleware, HITL and OpenTelemetry | Multi-agent workflow orchestration and restartable execution | **Continue the existing integration.** This repository already contains an isolated MAF checkpoint adapter and resume tests; adding a second framework install or replacing it would duplicate the current architecture. |
| [Swarms](https://github.com/kyegomez/swarms) | Apache-2.0 metadata; Python; active; broad agent/team orchestration and MCP integrations | Rapid experiments with hierarchical, sequential and concurrent agent patterns | **Do not adopt.** It introduces another orchestration runtime, model-provider dependencies and a broad dependency surface without solving the current scheduler, D1 durability or explicit opt-in gates. Reuse patterns only after a benchmark shows a concrete win. |
| [openJiuwen Agent Memory](https://github.com/openJiuwen-ai/agent-memory) | Apache-2.0 metadata; Python 3.11+; layered extraction/abstraction/association, hybrid retrieval and pluggable storage | More sophisticated long-term agent memory and retrieval evaluation | **Defer.** The project adds a memory service/indexing/storage subsystem. The engine already has D1 task/quota state and its own memory contracts; no measured retrieval-quality bottleneck justifies a second source of truth or an embedding/indexing service yet. |

### Adoption gate

No candidate is approved for dependency addition or production use by discovery score alone. Before adoption, verify the exact license file and transitive licenses, pin a release/commit, inspect dependency and network behavior, evaluate secrets/data handling, test on a disposable isolated branch, benchmark CPU/RAM/cold-start and provider spend, and compare against the existing runtime. A candidate that requires a paid model, paid sandbox, persistent VM, second queue, or second authoritative store is rejected under the current zero-spend architecture unless a measured benefit and separate approval justify it.

This review does not change dependencies, runtime configuration, agent roster, permissions, secrets, production services, or model-call policy.

## 5. Python agent frameworks

### Pydantic AI

Pydantic AI provides typed agent interfaces, tools, structured output and MCP clients for local stdio and remote Streamable HTTP/SSE. Its StepPersistence saves message-history snapshots, a step-event trail and a tool-effect ledger to a chosen store. This supports continuing/forking from the last complete snapshot, but it is not equivalent to automatic durable execution: an in-flight tool effect is left for the application to decide whether it is safe to retry.

For true crash recovery, Pydantic AI integrates with Temporal, DBOS, Prefect and Restate. Durable engines journal model/tool steps and replay completed work after restart. Side-effecting tools must be idempotent because an in-flight step may run again.

Recommendation: run a small no-provider comparison against the existing Microsoft Agent Framework adapter, focusing on tool schemas, structured outputs, step persistence and compatibility with the engine's policy wrapper. Do not adopt a second framework unless it wins with lower complexity.

### LangGraph

LangGraph is a low-level stateful orchestration framework. Checkpoints are identified by thread and checkpoint IDs; pending writes preserve successful node work when another node fails. It provides in-memory, SQLite and Postgres savers, with Postgres intended for production history.

Recommendation: borrow pending-write, per-thread isolation, explicit checkpoint ID and replay test ideas for the existing adapter. Do not add LangGraph to the runtime now; its orchestration/persistence layer overlaps current work.

### Agno

Agno documents team coordination modes, task decomposition, shared state, session history, memory, guardrails, human-in-the-loop, hooks, metrics and remote members. It is a broad alternative, not a small utility. Use its team/task examples as design references; prototype a missing capability independently rather than migrating the engine.

## 6. Coding-agent projects

### Goose

Goose provides a CLI, desktop app and API; supports MCP extensions, recipes, subagents and multiple model providers. It is useful as a reference for portable workflow recipes and extension patterns. It is a candidate for a future hosted or cloud workspace only if it demonstrates a measurable advantage over the existing GitHub-based workflow.

### OpenHands

OpenHands is better suited to a secured cloud Linux host than an Android tablet. Its self-hosting guide suggests a VM around 2 vCPU/4 GB RAM for one user and emphasizes firewall restrictions and API-key protection. Its Docker sandbox provides workspace isolation; running directly on the host gives the agent access to the host filesystem and network.

Recommendation: consider it only if a verified persistent cloud VM with Docker and network isolation becomes available. Do not expose agent/automation ports publicly without authentication, TLS, firewalling and workspace isolation.

## 7. Memory and retrieval

### Mem0

Mem0 OSS can run as a library or self-hosted Docker server. Its default library uses an OpenAI model and embeddings with local Qdrant and SQLite history; the server stack uses Postgres/pgvector. It is not a zero-dependency memory feature.

Decision: do not add now. First measure whether agents fail to retrieve relevant project memory.

### Letta

Letta's memory is git-backed: agents own a memory repository with always-in-context system blocks and on-demand notes. It also supports background consolidation. This is a useful pattern, but a separate Letta server would duplicate the existing project memory.

Decision: borrow the distinction between compact core memory and on-demand topic memory. The engine's existing SQLite memory and Git-backed documentation remain the baseline; do not add a separate memory service without benchmark evidence.

### Graphiti / Zep

Graphiti models temporal facts, relationships, validity windows and provenance episodes; it requires a graph database and LLM/embedder integration. Zep is a managed/governed enterprise context layer.

Decision: defer. Raawaa currently needs policy/task provenance and project knowledge, not a temporal graph. Reconsider when product, order, customer or campaign history creates a concrete temporal-reasoning requirement. Do not send customer data to external memory providers without privacy and consent controls.

### Qdrant

Qdrant supports dense and hybrid retrieval; Qdrant Edge offers embedded/offline retrieval. The server requires persistent block-level storage and network ports, with memory depending on vector count, dimensions, payloads and indexing. An always-on vector service is not appropriate for the 8 GB tablet.

Decision: defer vector search. Start with indexed Markdown/SQLite/D1 retrieval and add Qdrant Edge only if a benchmark demonstrates semantic-recall gains at acceptable memory cost.

## 8. Priority roadmap

| Priority | Work | Why | Gate |
|---|---|---|---|
| P1 | Cloudflare Workflows deterministic spike | Best fit for durable task execution on current platform | Local/CI only; no providers or deploy |
| P2 | Restart/resume and interrupted-write tests for file-backed checkpoint adapter | Prove persistence before D1 | Fresh adapter instance, HMAC verification, failure injection |
| P3 | Compare Pydantic AI StepPersistence with MAF adapter | Check whether a smaller agent runtime improves reliability | No-provider isolated benchmark |
| P4 | Improve memory retrieval using indexed Markdown/SQLite/D1 | Lower token use and better context selection | Retrieval benchmark before vector DB |
| P5 | Evaluate Qdrant Edge / Letta / Graphiti only against a demonstrated gap | Avoid premature infrastructure | Measured retrieval or temporal-memory need |

## 9. Additional self-hosted and GitHub-native agent platforms — reviewed 2026-11-03

This pass compared four actively discussed platforms against the engine's existing D1 task ledger, Cloudflare control Worker, bounded GitHub Actions consumer, quota policy, and mobile-only operating constraint. Repository metadata and upstream setup/release documentation were checked; no code was cloned, installed, or executed.

| Platform | Verified upstream facts | Relevant capability | Main mismatch | Decision |
|---|---|---|---|---|
| [OmniGent](https://github.com/omnigent-ai/omnigent) | Apache-2.0, Python, 11,176 stars / 1,840 forks at review time; pushed 2026-11-03; upstream marks the project alpha. Requires Python 3.12+, Node 22+, pnpm and host tooling. | Meta-harness for Claude Code, Codex, Cursor, OpenCode, Hermes, Pi and custom YAML agents; phone/browser UI; policy scopes and sandboxing; multiple cloud sandbox adapters. Includes a Cloudflare Containers + D1 + R2 deployment guide. | The Cloudflare deployment requires the Workers Paid plan, a local Docker build, D1 API token, R2 S3 credentials and a separate connected host to execute agents. The container is a single-replica control plane; it does not make model inference or sandbox execution free. It would duplicate our current D1/Worker control plane and still needs a persistent host/provider. | **Do not adopt as a second runtime.** Strongest design reference in this pass for mobile supervision, harness adapters, per-agent policy, and sandbox boundaries. Reuse patterns only where they strengthen the existing engine without duplicating its queue, database or scheduler. |
| [Optio](https://github.com/jonwiggins/optio) | MIT, TypeScript, 1,086 stars / 125 forks at review time; pushed 2026-11-02; latest GitHub release v0.23.0 on 2026-10-29. | Work feed for agent tasks/jobs, schedules and event triggers; Git worktrees; CI/review/resume; persistent agents; native iOS and Android clients; paired-machine execution. | Control plane requires Kubernetes, PostgreSQL and Redis. The local quick-start requires Docker Desktop with Kubernetes, Node.js, pnpm, Helm and kubectl. Native mobile clients do not remove the server/cluster requirement. | **Do not adopt now.** Strong reference for mobile operations UX, work-feed design and resumable PR workflows. Infrastructure is incompatible with the current no-PC / zero-spend baseline unless a suitable persistent cluster is later verified. |
| [SwarmClaw](https://github.com/swarmclawai/swarmclaw) | MIT, TypeScript, 703 stars / 144 forks; repository last pushed 2026-06-30; latest GitHub release v1.9.40 was published 2026-06-30. | Self-hosted agent runtime with durable memory, MCP, skills, schedules, delegation, structured sessions and 24+ provider/CLI integrations; publishes ARM64 container images. | Requires a persistent Node.js host (Node 22.6+); Docker is recommended for sandboxing. The site describes a broad provider/runtime surface, but there is no verified always-free persistent host for this project. GitHub push/release recency is materially older than the current review date. | **Monitor only.** Useful reference for MCP assignment, skill registry and durable sessions; do not add a second runtime, database or model router. Recheck upstream maintenance before reconsidering. |
| [Aeon](https://github.com/aeonfun/aeon) | MIT, JavaScript, 787 stars / 297 forks; pushed 2026-11-02; first tagged release v0.1.0 published 2026-07-09. | GitHub Actions-native scheduled skills, persistent repo-backed memory, reactive triggers, skill health scoring, self-repair PRs, and an optional MCP interface. The current repository tree contains 88 skill definitions across six category packs; `docs/CORE.md` identifies roughly 15 load-bearing skills. The heartbeat skill is the only skill enabled by default; the rest ship off until individually enabled. | It still requires a model/provider route for agent work and its health scoring uses a model; public Actions minutes do not make inference free. The provider cascade includes external services, and the catalog contains consequential actions (e.g. token distribution and contract deployment). The checked-in configuration enables the heartbeat skill by default and leaves the remaining skill definitions off. Its security policy treats fetched web content as untrusted and warns that skills run with access to repository secrets; third-party skill packs install disabled and require review before activation. | **Do not adopt wholesale.** Closest architectural reference because it uses GitHub Actions. Borrow only bounded patterns: explicit read-only/write capability modes, declared secret requirements, quality/failure scoring, systemic failure clustering, cooldowns, and one small repair PR per cycle. Keep all provider calls and consequential skills behind Raawaa's existing zero-budget and approval gates. |

### Transferable patterns versus duplication

The current engine already owns the canonical task state, D1 audit trail, agent roster, policy loader, quota ledger, provider profiles, and bounded scheduler. Therefore:

- Do not import a second scheduler, memory store, model router, or orchestration framework from these platforms. OmniGent's Cloudflare deployment is a paid Containers path and still requires a separate host; it is not a zero-cost shortcut for the current Worker.
- Consider borrowing OmniGent's layered policy idea as a future additive capability contract per agent/task: `read_only` / `internal_write` / `external_write`, permitted data classifications, required secret *names*, allowed task types, and budget class. Enforce it in the existing policy loader and handler boundary; a prompt-only declaration is insufficient.
- Consider a model-free health evaluator first: task exit codes, retry/error signatures, staleness, artifact schema checks and CI outcomes. Model-based quality scoring remains disabled until a zero-cost, account-verified route is explicitly approved.
- Keep automated repair limited to a branch + tested PR. Never allow an autonomous repair skill to silently modify secrets, production settings, billing, trading, or user-facing storefront content.

### Sources checked

- Optio repository and [getting started requirements](https://optio.host/docs/getting-started/).
- [SwarmClaw repository](https://github.com/swarmclawai/swarmclaw), [documentation](https://www.swarmclaw.ai/docs), and [release history](https://github.com/swarmclawai/swarmclaw/releases).
- [OmniGent repository](https://github.com/omnigent-ai/omnigent), [Cloudflare deployment guide](https://github.com/omnigent-ai/omnigent/blob/main/deploy/cloudflare/README.md), and [security policy](https://github.com/omnigent-ai/omnigent/blob/main/SECURITY.md).
- [Aeon repository](https://github.com/aeonfun/aeon), [core architecture](https://github.com/aeonfun/aeon/blob/main/docs/CORE.md), [configuration](https://github.com/aeonfun/aeon/blob/main/aeon.yml), and [security policy](https://github.com/aeonfun/aeon/blob/main/.github/SECURITY.md).

## 10. Cloudflare-native durable agents and browser automation — reviewed 2026-11-03

### Cloudflare Agents SDK

Cloudflare's official Agents documentation (updated 2026-10-29) describes each Agent as a Durable Object with a Lifecycle runtime. The Lifecycle owns startup, event dispatch, a durable job queue and the object's single alarm. Optional capabilities include durable scheduling (delayed, dated, cron and interval), shared state, WebSockets, MCP connection management and observability; Streams and Sessions are marked experimental. The official scheduler persists tasks in SQLite and wakes the Durable Object through alarms.

**Decision: do not migrate the current engine to Agents SDK now.** The current Worker already has a narrow Cron Trigger and D1 audit heartbeat; the engine already has a D1 task ledger, Python task consumer, and bounded GitHub Actions runner. Introducing Durable Objects, a second SQLite scheduler, and a new lifecycle would duplicate state and increase migration surface without solving the present blocker: GitHub Actions has not yet emitted a post-resync `schedule` event for the Python Task Runner. Reconsider only if a measured need for durable per-agent sessions, WebSocket state, or long-lived agent jobs emerges. Keep D1 as the canonical task/audit ledger unless a migration plan proves otherwise.

### Browser automation replacement decision

| Option | Verified facts | Operational fit | Decision |
|---|---|---|---|
| [Microsoft Playwright MCP](https://github.com/microsoft/playwright-mcp) | Open-source MCP server; release v0.0.84 was published 2026-10-31. Its release notes include isolated browser contexts for separate accounts. | Can run in an ephemeral GitHub Actions Linux runner; browser install and runner minutes still consume compute. No hosted browser account is required for local execution. Authenticated profiles must not be committed to the repository. | **Preferred deterministic browser fallback** when an API does not exist. Keep optional and run only for a specific task. |
| [Stagehand](https://github.com/browserbase/stagehand) | MIT-licensed browser-agent SDK; combines semantic `act`, `observe`, `extract`, and `agent` with local Chromium. Browserbase-hosted sessions are optional. | Local execution avoids hosted-browser fees, but semantic actions invoke a model and therefore may incur inference cost; deterministic Playwright controls remain cheaper and easier to audit. | Use only for a site where DOM changes make deterministic automation demonstrably brittle, and only with a verified zero-cost model route. |
| [Browser Use](https://github.com/browser-use/browser-use) | Open-source Python browser agent built on Playwright. | Agentic browser actions still need an inference route and browser compute; persistent browser state needs secure storage. | Monitor; do not add a second browser stack while Playwright is sufficient. |

**Control policy for any future browser runner**
- API-first remains the default: GitHub API/connector for repository operations, Cloudflare Worker API for engine state, and direct provider APIs where supported.
- Browser automation is a last-mile adapter, not a replacement for APIs or the task ledger.
- Start with read-only navigation and extraction; allow writes only when a task contract explicitly permits them.
- Isolate browser contexts by account; do not log cookies, credentials, tokens, or private page contents.
- Do not store authenticated browser profiles in Git, Actions artifacts, or unencrypted D1.
- Use deterministic selectors/accessibility snapshots for stable tasks; use semantic model-driven actions only when needed and after checking the model budget.
- Never use browser automation to bypass authentication, rate limits, CAPTCHAs, or access controls.

### Scheduling reliability finding

GitHub's current workflow troubleshooting guidance confirms scheduled events are best-effort: high-load periods can delay or drop queued jobs, and a schedule can stop while manual dispatch still works. GitHub also documents that a schedule may need a cron-definition commit to restart after the associated actor is suspended, deleted or deprovisioned.

This supports keeping the minute-4 phase change as a recovery attempt, but it does not establish that the Task Runner schedule has recovered. The first new successful `schedule`-triggered Task Runner run is still the acceptance criterion. A Worker Cron heartbeat is separate evidence and must not be conflated with Python task processing.

### Sources checked
- [Cloudflare Agents getting started](https://developers.cloudflare.com/agents/getting-started/) and [runtime](https://developers.cloudflare.com/agents/runtime/).
- [Cloudflare Agents scheduler implementation](https://github.com/cloudflare/agents/blob/main/docs/agents/scheduling.md).
- [Playwright MCP releases](https://github.com/microsoft/playwright-mcp/releases).
- [Stagehand](https://github.com/browserbase/stagehand).
- [Browser Use](https://github.com/browser-use/browser-use).
- [GitHub Actions workflow troubleshooting](https://docs.github.com/en/actions/how-tos/troubleshoot-workflows) and [workflow syntax](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax).

## 11. Rust-native self-hosted agent runtimes — GitHub metadata verified 2026-11-03

Two additional projects were checked against their actual GitHub repository metadata, rather than relying on product-site claims alone.

| Project | GitHub evidence at review time | Capabilities claimed by upstream | Decision for Raawaa |
|---|---|---|---|
| [Garudust Agent](https://github.com/garudust-org/garudust-agent) | MIT; 31 stars, 10 forks; latest push 2026-07-27; latest release `v0.13.8` on 2026-06-13. | Rust agent runtime; ARM64 Linux release asset; MCP, persistent memory, skill registry, cron, browser/terminal tools, provider routing and REST/WebSocket server. | **Monitor, do not deploy.** The feature overlap is substantial, but its latest release and repository activity are older than the current date. No verified Android/Termux deployment or always-free persistent host was established. |
| [Ryvos](https://github.com/Ryvos/ryvos) | MIT; 8 stars, 2 forks; latest push 2026-07-25; latest release `v0.9.0` on 2026-04-16. | Rust single-binary runtime; upstream README claims 86+ tools, 18+ providers, MCP, persistent memory, cron, DAG workflows, budget tracking, browser automation and sandboxing. | **Monitor, do not deploy.** Attractive compact-runtime and security concepts, but the small repository footprint, older release cadence, persistent-host requirement, and unverified Android deployment make adoption premature. |
 
### Transferable design ideas
- Capability-level tool permissions and separate Viewer/Operator/Admin roles.
- Per-run budgets, explicit stop conditions, checkpoint/resume and watchdogs.
- MCP adapters as optional integrations, with tool-level schemas and declared permissions.
- Skills with manifests and validation, rather than executing arbitrary downloaded instructions.
- Isolated terminal/browser execution with secrets withheld from tool output.

These ideas are references only. The engine must retain its own task ledger, quota controls, zero-budget default, dry-run mode, and approval policy. Do not import a complete second agent runtime or treat upstream security claims as a substitute for an independent threat review.

## 11. Daily public-source discovery review — 2026-11-03 01:24 UTC

### Discovery pipeline evidence

The new [Scheduled Open-Source Research Discovery workflow](https://github.com/ghith93/raawaa-autonomous-engine/actions/runs/41559511028) ran successfully on main SHA `4800d2aa3fb9dcd31c7dd0aaffe2e4d1b1b5638e`. It completed six bounded GitHub repository searches and one Crossref query, yielding 26 public repository metadata records and 10 scholarly metadata records. The first-pass deterministic triage produced eight high-priority manual-review candidates. The run made no model calls, installed or executed no candidate code, and uploaded two bounded artifacts. D1 persistence was skipped because the explicit persistence variable was not enabled. The workflow retains artifacts for 30 days.

The triage score is a discovery-priority heuristic, not a security, quality, or adoption verdict. Stars, recent commits and license metadata do not establish maintainer quality, license compliance, safe code, or compatibility. Every candidate remains `unreviewed`; automatic adoption is disabled.

### Highest-value candidates and decisions

| Candidate | Discovery signal | Relevant capability | Decision for Raawaa |
|---|---|---|---|
| [Omnigent](https://github.com/omnigent-ai/omnigent) | Apache-2.0; about 11.1k stars in the discovery snapshot; Python 3.12+ | Harness-neutral orchestration across Codex, Claude Code, Cursor, OpenCode, Pi and custom agents; mobile session UI; sandbox adapters; policy governance; MCP gateway. Telemetry is enabled by default. | **Borrow architecture patterns, do not deploy the platform.** Its remote sessions and harness abstraction are relevant, but the full server plus sandbox providers and model credentials add infrastructure and provider dependencies that conflict with the current free-first Cloudflare/GitHub design. Review telemetry opt-out and sandbox boundaries before any experiment. |
| [CrewAI](https://github.com/crewAIInc/crewAI) | MIT; about 60.4k stars in the snapshot | Standalone Crews and deterministic Flows, task delegation and integrations. | **Do not add as a runtime.** It would duplicate the current task store, deterministic planner and specialist assignment. Reuse concepts only if a concrete workflow gap appears. |
| [Swarms](https://github.com/kyegomez/swarms) | Apache-2.0; about 7.3k stars in the snapshot | Sequential, concurrent, graph and hierarchical orchestration; MCP deployment and many composition patterns. | **Do not add as a runtime.** The breadth is useful as a design catalogue, but it introduces a large agent framework and model/provider assumptions. Our existing policy and task ledger must remain the execution authority. |
| [Workflow Builder](https://github.com/synergycodes/workflowbuilder) | Apache-2.0; about 427 stars in the snapshot | White-label React workflow editor, Temporal durability, per-node retry/timeout, live run history and human approvals. | **Defer.** It requires Temporal and Postgres for durable execution and is aimed at embedded workflow editors. The current mobile status surface and D1 ledger are a smaller fit; revisit only if visual workflow authoring becomes a real requirement. |
| [JiuwenMemory](https://github.com/openJiuwen-ai/agent-memory) | Apache-2.0; about 76 stars in the snapshot | L0–L3 layered memory, asynchronous consolidation, graph memory, conflict handling, REST/MCP service and multiple storage adapters. | **Do not replace current memory.** The architecture is worth monitoring, but graph/vector retrieval and automatic memory extraction add storage and embedding/LLM dependencies. The existing SQLite memory remains cheaper and simpler until a measured retrieval failure justifies expansion. |
| [Shipwright](https://github.com/app-vitals/shipwright) | MIT; early-stage in the snapshot | Plan → build → review → metrics delivery pipeline; GitHub Actions or self-hosted execution; layered unit/integration/smoke/E2E testing. | **Borrow its durable artifact and test-readiness discipline.** Do not adopt its Claude Code-centric execution dependency; the engine already uses reviewed GitHub PRs and CI. |
| [LittleGuys](https://github.com/herval/littleguys) | MIT; Rust; small adoption signal | Lightweight coding-agent experimentation. | **Monitor only.** No demonstrated capability gap or mobile-first deployment advantage. |
| [durable-workflow/workflow](https://github.com/durable-workflow/workflow) | MIT; PHP | Durable workflow engine. | **Do not adopt.** Language/runtime mismatch and no clear advantage over the existing D1 + Worker + Actions split. |

### Research literature

Crossref returned 10 records. Most were foundational or older multi-agent work with modest citation signals. The newest finance-related result was *A Parent-Child Multi-Agent Architecture for Real-Time Financial Fraud Detection Using Tool-Augmented Language Model Agents* (2026 SSRN DOI `10.2139/ssrn.7377878`); it is a preprint-level research lead, not evidence of production-grade arbitrage performance. The recent 2026 paper on multi-agent decision-conference agreement is relevant to reviewer consensus, but its initial citation count is zero. Neither paper justifies enabling financial execution or adding a new runtime.

### Concrete adoption decisions

1. Keep the existing D1 task ledger, Python worker, GitHub Actions runner, Cloudflare Worker and specialist roster as the only production control plane.
2. Keep Omnigent as the strongest future reference for harness adapters, remote mobile sessions and policy-enforced sandboxing; do not import its dependency graph.
3. Keep CrewAI and Swarms as architecture references only. Do not create parallel orchestrators.
4. Keep JiuwenMemory as a future memory-evaluation candidate; do not introduce vector databases, Redis or model-based memory consolidation while all provider budgets are zero.
5. Use Shipwright's plan/build/review artifact chain as a process reference, not a runtime dependency.
6. Continue daily public-metadata discovery, but require manual license, security, maintenance, cost, mobile compatibility and architecture review before any adoption.

### Sources

- [Omnigent repository](https://github.com/omnigent-ai/omnigent) and [architecture overview](https://omnigent.ai/).
- [CrewAI repository](https://github.com/crewAIInc/crewAI).
- [Swarms repository](https://github.com/kyegomez/swarms).
- [Workflow Builder repository](https://github.com/synergycodes/workflowbuilder).
- [JiuwenMemory repository](https://github.com/openJiuwen-ai/agent-memory) and [release history](https://github.com/openJiuwen-ai/agent-memory/releases).
- [Shipwright repository](https://github.com/app-vitals/shipwright).
- [LittleGuys repository](https://github.com/herval/littleguys).
- [Crossref public REST API](https://api.crossref.org/).


## 11. Python agent runtimes and skill governance — reviewed 2026-11-03

### Framework comparison against the current engine

The current production architecture is deliberately framework-light: deterministic Python task contracts, D1 task/lease state, a bounded GitHub Actions consumer, policy enforcement, a quota-aware model gateway, and a separate Cloudflare Worker. The agent roster and workflow planner do not themselves execute model-driven agents. The Microsoft Agent Framework remains an isolated evaluation with zero provider requests; its checkpoint adapter is a test prototype, not production D1 storage.

| Candidate | Verified current signal | Strength | Main cost / risk in this repository | Decision |
|---|---|---|---|---|
| [PydanticAI](https://github.com/pydantic/pydantic-ai) | v2.65.0 released 2026-11-03 | Typed Python agent contracts, structured outputs, provider flexibility, validation and evaluation ecosystem | Must integrate its model calls through the existing quota-aware gateway; otherwise it bypasses budgets, classification and the usage ledger. | **Best next isolated agent-loop candidate**, not a production dependency yet. |
| [OpenAI Agents SDK](https://github.com/openai/openai-agents-python) | v0.24.0 released 2026-10-15 | Small primitives for agents, tools, handoffs, guardrails, sessions, MCP and built-in tracing | OpenAI-first operational path; direct SDK use could bypass the engine gateway. Trace content and tool outputs require redaction and retention review. | Keep as the alternate if first-party OpenAI orchestration is materially better in a measured task. |
| [LangGraph](https://github.com/langchain-ai/langgraph) | 1.2.19 released 2026-10-30; SQLite checkpoint package 3.1.3 | Explicit state graph, checkpointing, interrupts and recovery for complex branching workflows | Overlaps the existing deterministic planner, D1 task ledger and Workflow spike; more concepts and state adapters to maintain. | Defer until graph complexity exceeds the current DAG planner and a benchmark demonstrates value. |
| [Temporal Python SDK](https://github.com/temporalio/sdk-python) | 1.35.0 released 2026-10-28 | Mature event-history replay and durable orchestration across services | Requires a Temporal server/Cloud service and operational ownership; this is a second durable scheduler and may add recurring infrastructure cost. | Do not adopt for the current single bounded consumer. Revisit for multi-service long-running workflows. |
| [Microsoft Agent Framework](https://github.com/microsoft/agent-framework) | Existing isolated evaluation pins core 1.24.0 and OpenAI 1.18.0 | Parallel workflow branches and checkpoint/resume prototype already tested in this repository | Durable storage is not implemented; its direct provider client can bypass the engine gateway. | Keep the current evaluation; do not expand production scope until a secure D1 adapter and gateway integration pass. |
| [Cloudflare Agents SDK](https://developers.cloudflare.com/agents/) | Durable Object lifecycle with SQLite-backed state, alarms and fibers | Useful for addressable long-lived agent identities and event-driven sessions | TypeScript/DO runtime and another state store/scheduler would duplicate D1 + current Worker Cron and increase migration surface. | Defer; retain the existing Cloudflare Worker as the narrow control plane. |

### Runtime decision

Do not add a second orchestrator, task queue, scheduler, memory database, or provider gateway. Keep the existing D1 task ledger and Python consumer as the canonical execution path. When the live-inference gate is explicitly enabled for a public-only research task, run a small isolated PydanticAI spike first, with a custom model adapter that calls only `QuotaAwareLiveGateway`. Compare it against the existing Microsoft Agent Framework evaluation and a direct minimal gateway loop.

Acceptance criteria for any runtime experiment:
1. All provider requests pass through the existing classification gate, atomic quota reservation, usage ledger, and kill switch.
2. A fake provider proves the workflow without network calls; live calls remain disabled.
3. Typed tool arguments and outputs are schema-validated and deny-by-default.
4. Tool execution is separated from model decisions; external writes require operation-specific policy and approvals.
5. Interrupt/resume, duplicate delivery, timeout, retry and idempotency are tested.
6. Traces/logs exclude credentials, private task payloads and confidential business data.
7. The experiment shows a measurable improvement over the current baseline before any production dependency is added.

### Agent Skills open standard

The [Agent Skills specification](https://agentskills.io/specification) defines a portable directory with a required `SKILL.md` containing YAML frontmatter (`name`, `description`) and optional scripts, references and assets. Optional `allowed-tools` metadata is experimental and support varies by agent implementation. It is a packaging/discovery convention, **not** a runtime authorization boundary or a sandbox.

The repository currently has no local `skills/` runtime or skill loader. ChatGPT's connected environment exposes 248 skill definitions across 31 skill packs, but those definitions and connected-app credentials do not automatically exist inside the standalone Python engine. Do not copy third-party skills into the engine and execute them merely because they are available in ChatGPT.

### Skill and tool supply-chain security baseline

The [OWASP Agentic Skills Top 10](https://github.com/OWASP/www-project-agentic-skills-top-10) highlights risks including malicious skills, supply-chain compromise, excessive privileges, unsafe metadata, untrusted external instructions, weak isolation, update drift, poor scanning, missing governance and cross-platform reuse. Apply the following baseline before introducing an engine-local skill registry:

- Inventory every skill with canonical source URL, immutable commit/version, licence, owner, review date and content hash.
- Parse YAML with safe loaders; validate required fields and size/type limits before reading skill instructions.
- Treat skill text, linked documents, repository READMEs, browser results and model outputs as untrusted data. They cannot override the trusted operating policy.
- Do not fetch mutable remote instructions at runtime. Vendor or pin reviewed references to an immutable revision and rescan updates.
- Never treat `allowed-tools` or prompt text as enforcement. Enforce tool permissions in the backend with per-operation allowlists and deny-by-default schemas.
- Do not execute skill scripts by default. If a future skill needs scripts, run them in an isolated ephemeral environment with read-only source mounts, minimal network, no ambient credentials, CPU/time/output limits and auditable results.
- Keep secrets outside skill folders and prompts. Inject short-lived, task-scoped credentials only into the one approved adapter that needs them.
- Require independent review for skills that can access credentials, write repositories, deploy, message users, move money or modify customer data.
- Record skill source, hash, version, invoking task, tool calls and sanitized result metadata in the audit trail.

### Research sources checked

- [PydanticAI releases](https://github.com/pydantic/pydantic-ai/releases) — v2.65.0 (2026-11-03).
- [OpenAI Agents SDK releases](https://github.com/openai/openai-agents-python/releases) — v0.24.0 (2026-10-15).
- [LangGraph releases](https://github.com/langchain-ai/langgraph/releases) — 1.2.19 (2026-10-30).
- [Temporal Python SDK releases](https://github.com/temporalio/sdk-python/releases) — 1.35.0 (2026-10-28).
- [Agent Skills specification](https://agentskills.io/specification) and [reference repository](https://github.com/agentskills/agentskills).
- [OWASP Agentic Skills Top 10](https://github.com/OWASP/www-project-agentic-skills-top-10) and [OWASP AI Agent Security Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/AI_Agent_Security_Cheat_Sheet.html).


## 12. Skill supply-chain scanners and agent evaluation projects — reviewed 2026-11-03

The broader GitHub searches surfaced the following concrete projects. Repository metadata and READMEs were inspected directly; popularity is not treated as a security or quality guarantee.

| Project | Verified details | Fit for Raawaa | Decision |
|---|---|---|---|
| [NVIDIA SkillSpector](https://github.com/NVIDIA/SkillSpector) | Apache-2.0; Python 3.12+; static and optional semantic skill scanning; 71 patterns across 17 categories; AST/dataflow, YARA signatures, JSON/Markdown/SARIF output; `--no-llm` disables LLM analysis. Includes explicit analysis resource bounds. | Matches the engine's Python 3.12 CI and can run without provider inference. | **Preferred scanner to pilot when an engine-local `skills/` directory exists.** Pin a reviewed release/commit and use static-only mode in CI. Do not add it while there are no local skills to scan. |
| [Cisco AI Defense Skill Scanner](https://github.com/cisco-ai-defense/skill-scanner) | Python 3.11–3.14; README claims Apache-2.0; combines YAML/YARA-X, AST/dataflow and optional LLM analysis; supports SARIF, GitHub Actions and pre-commit. GitHub repository metadata currently reports `NOASSERTION` for its licence. | Technically promising second opinion; licence metadata needs reconciliation before vendoring or installing. | Keep as an alternate; verify the actual LICENSE file and pinned release before adoption. |
| [Snyk Agent Scan](https://github.com/snyk/agent-scan) | Apache-2.0; scans agent components, MCP servers and skills. README warns that MCP scanning may execute configured commands or make outbound network requests; CLI output is explicitly experimental. | Useful inventory/scan reference, but its MCP discovery mode is unsafe to point at untrusted configs without isolation. | Do not use as the default CI scanner; reconsider only inside a disposable sandbox with reviewed configuration. |
| [Microsoft SkillOpt](https://github.com/microsoft/SkillOpt) | MIT; Python 3.10+; optimizes reusable skill text from agent trajectories, with replay and held-out validation gates. Supports multiple model backends. | Potentially useful for measured skill improvement, but needs real model calls, repeatable task trajectories and evaluation data. | Defer until live inference is explicitly enabled and a local skill registry exists. |
| [SkillsBench](https://github.com/benchflow-ai/skillsbench) | Apache-2.0; evaluates whether agents actually benefit from skills. Uses BenchFlow tasks and supports local Docker; Modal is the default cloud sandbox in its quick start. | Good future benchmark for skill usefulness, but real agent runs need model credentials and execution resources. | Use its task/evaluation design later; do not add Modal or model spend now. |
| [PawBench](https://github.com/agentscope-ai/PawBench) | Apache-2.0; v1.0 describes 150 tasks, 9 models and 3 harnesses with public prompts, graders, traces and diagnostic slices. | Strong model × harness evaluation design; requires multiple model runs and a judge to reproduce its full comparison. | Use as a benchmark-design reference, not as a production dependency. |
| [AgentCompass](https://github.com/open-compass/AgentCompass) | Apache-2.0; Python 3.12+; README describes 30+ benchmarks, 10+ harnesses, local/Docker/remote sandboxes, task scheduling, trace capture and resumable evaluations. | Broad evaluation infrastructure, but overlaps the existing planner, D1 task ledger and CI harness. | Defer; too much additional runtime and dependency surface for current needs. |
| [Cloudflare Security Audit Skill](https://github.com/cloudflare/security-audit-skill) | MIT; multi-phase coding-agent security-audit skill with independently verified machine-readable findings. | Useful future audit procedure, but it is a skill rather than a standalone scanner. | Review manually before any import; do not treat it as an authorization boundary. |

### Recommended skill-security path

1. Keep the current engine with no executable local skills until a real agent runtime consumes them.
2. When the first local skills are added, create a small reviewed registry using the Agent Skills directory format and immutable source/version/hash metadata.
3. Pilot NVIDIA SkillSpector with `--no-llm` on only that local directory in an ephemeral CI runner. Preserve SARIF/JSON findings as artifacts; do not fail the build on unreviewed false positives until a baseline is established.
4. Add human review and the engine's backend tool allowlist. Scanner output is advisory; it never grants permissions.
5. Add SkillsBench-style golden tasks only when the runtime can execute deterministic, budget-bounded test cases. Measure skill-on vs skill-off, task completion, tool correctness, latency and cost.
6. Consider SkillOpt only after the engine has enough reviewed trajectories to optimize against and a held-out validation set to prevent skill regressions.

**Do not install any scanner or skill package globally on the user's devices.** The first pilot should run only in disposable GitHub Actions compute, with no repository secrets passed to the scanner and no LLM mode.

## Adoption rule

No framework, agent, plugin, vector database or hosted provider is adopted because it is popular or feature-rich. It must solve a measured limitation, fit zero-spend or have a separately approved budget, preserve policy/quota enforcement, support bounded execution and auditability, have a maintained upstream and clear license, and pass isolated tests before production integration.

## Sources

- [Cloudflare Workflows overview](https://developers.cloudflare.com/workflows/)
- [Cloudflare Python Workers overview](https://developers.cloudflare.com/workers/languages/python/)
- [Python Workers standard library and unsupported modules](https://developers.cloudflare.com/workers/languages/python/stdlib/)
- [Query D1 from Python Workers](https://developers.cloudflare.com/d1/examples/query-d1-from-python-workers/)
- [Cloudflare Python Workers examples](https://github.com/cloudflare/python-workers-examples)
- [Cloudflare Agents SDK long-running agents](https://developers.cloudflare.com/agents/concepts/agentic-patterns/long-running-agents/)
- [Cloudflare Agents SDK durable execution](https://developers.cloudflare.com/agents/agent-class/durable-execution/)
- [Cloudflare Workflows getting started](https://developers.cloudflare.com/workflows/get-started/guide/)
- [Cloudflare Queues limits](https://developers.cloudflare.com/queues/platform/limits/)
- [Pydantic AI durable execution](https://pydantic.dev/docs/ai/capabilities/durable_execution/overview/)
- [Pydantic AI MCP client](https://github.com/pydantic/pydantic-ai/blob/main/docs/mcp/client.md)
- [LangGraph checkpointing](https://langchain-ai.github.io/langgraph/reference/checkpoints/)
- [Agno teams](https://docs.agno.com/examples/teams/overview)
- [Goose](https://block.github.io/goose/)
- [OpenHands self-hosting](https://github.com/OpenHands/OpenHands/blob/main/docs/SELF_HOSTING.md)
- [OpenHands Docker sandbox](https://github.com/OpenHands/docs/blob/main/openhands/usage/sandboxes/docker.mdx)
- [Mem0 OSS overview](https://github.com/mem0ai/mem0/blob/main/docs/open-source/overview.mdx)
- [Letta Agent SDK](https://www.letta.com/agent-sdk/)
- [Graphiti](https://github.com/getzep/graphiti)
- [Qdrant installation](https://qdrant.tech/documentation/installation/)

**Release freshness check (2026-11-03):** The latest release tags were verified directly against the GitHub Releases API: PydanticAI `v2.65.0` (published 2026-11-03 01:25 UTC) and Temporal Python SDK `1.35.0` (published 2026-10-28 20:16 UTC). This does not change the adoption decision: evaluate PydanticAI in an isolated prototype only; do not add a production dependency until its quota, retry, policy, and D1 integration are tested.
