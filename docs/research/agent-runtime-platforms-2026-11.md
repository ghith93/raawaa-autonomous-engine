# Agent Runtime and Memory Projects — Evaluation for Raawaa Engine

**Research date:** 2026-11-02
**Decision:** Keep the existing engine control plane. Prototype Cloudflare Workflows next. Do not install another full agent framework or vector database until a measured gap justifies it.

## Executive decision

Raawaa has a policy loader, task model, quota controls, D1-backed state, a Microsoft Agent Framework checkpoint adapter, GitHub Actions, and no required device worker. Adding another full orchestration framework now would duplicate scheduling, persistence, memory and approval logic. Cloudflare Workflows has a local runtime prototype, but production adoption still requires separate D1 durability and recovery validation.

## Candidate comparison

| Project | Strength | Fit | Decision |
|---|---|---|---|
| Cloudflare Workflows | Durable multi-step Workers, retries, persisted state, pauses and event-driven resume | High: existing Cloudflare Worker/D1 environment; available on Free and Paid plans | **Prototype next** with deterministic fake tasks |
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

## 2. Python agent frameworks

### Pydantic AI

Pydantic AI provides typed agent interfaces, tools, structured output and MCP clients for local stdio and remote Streamable HTTP/SSE. Its StepPersistence saves message-history snapshots, a step-event trail and a tool-effect ledger to a chosen store. This supports continuing/forking from the last complete snapshot, but it is not equivalent to automatic durable execution: an in-flight tool effect is left for the application to decide whether it is safe to retry.

For true crash recovery, Pydantic AI integrates with Temporal, DBOS, Prefect and Restate. Durable engines journal model/tool steps and replay completed work after restart. Side-effecting tools must be idempotent because an in-flight step may run again.

Recommendation: run a small no-provider comparison against the existing Microsoft Agent Framework adapter, focusing on tool schemas, structured outputs, step persistence and compatibility with the engine's policy wrapper. Do not adopt a second framework unless it wins with lower complexity.

### LangGraph

LangGraph is a low-level stateful orchestration framework. Checkpoints are identified by thread and checkpoint IDs; pending writes preserve successful node work when another node fails. It provides in-memory, SQLite and Postgres savers, with Postgres intended for production history.

Recommendation: borrow pending-write, per-thread isolation, explicit checkpoint ID and replay test ideas for the existing adapter. Do not add LangGraph to the runtime now; its orchestration/persistence layer overlaps current work.

### Agno

Agno documents team coordination modes, task decomposition, shared state, session history, memory, guardrails, human-in-the-loop, hooks, metrics and remote members. It is a broad alternative, not a small utility. Use its team/task examples as design references; prototype a missing capability independently rather than migrating the engine.

## 3. Coding-agent projects

### Goose

Goose provides a CLI, desktop app and API; supports MCP extensions, recipes, subagents and multiple model providers. It is useful as a reference for portable workflow recipes and extension patterns. Installing it on the tablet would duplicate AndroidHarness's Code agent, shell, Git, browser, skills, nested agents and MCP client.

### OpenHands

OpenHands is better suited to a secured cloud Linux host than an Android tablet. Its self-hosting guide suggests a VM around 2 vCPU/4 GB RAM for one user and emphasizes firewall restrictions and API-key protection. Its Docker sandbox provides workspace isolation; running directly on the host gives the agent access to the host filesystem and network.

Recommendation: consider it only if a verified persistent cloud VM with Docker and network isolation becomes available. Do not expose agent/automation ports publicly without authentication, TLS, firewalling and workspace isolation.

## 4. Memory and retrieval

### Mem0

Mem0 OSS can run as a library or self-hosted Docker server. Its default library uses an OpenAI model and embeddings with local Qdrant and SQLite history; the server stack uses Postgres/pgvector. It is not a zero-dependency memory feature.

Decision: do not add now. First measure whether agents fail to retrieve relevant project memory.

### Letta

Letta's memory is git-backed: agents own a memory repository with always-in-context system blocks and on-demand notes. It also supports background consolidation. This is a useful pattern, but a separate Letta server would duplicate the existing project memory.

Decision: borrow the distinction between compact core memory and on-demand topic memory. The repository's `.harness/memory.md`, project skill and Git-backed docs already provide a simpler implementation.

### Graphiti / Zep

Graphiti models temporal facts, relationships, validity windows and provenance episodes; it requires a graph database and LLM/embedder integration. Zep is a managed/governed enterprise context layer.

Decision: defer. Raawaa currently needs policy/task provenance and project knowledge, not a temporal graph. Reconsider when product, order, customer or campaign history creates a concrete temporal-reasoning requirement. Do not send customer data to external memory providers without privacy and consent controls.

### Qdrant

Qdrant supports dense and hybrid retrieval; Qdrant Edge offers embedded/offline retrieval. The server requires persistent block-level storage and network ports, with memory depending on vector count, dimensions, payloads and indexing. An always-on vector service is not appropriate for the 8 GB tablet.

Decision: defer vector search. Start with indexed Markdown/SQLite/D1 retrieval and add Qdrant Edge only if a benchmark demonstrates semantic-recall gains at acceptable memory cost.

## 5. AndroidHarness is optional diagnostics only (superseded)

The previous recommendation to use AndroidHarness Code as a tablet-side execution surface is superseded. The installed version is 3.1 and the operator chose to remove the device from the critical path. Use GitHub Actions for reproducible CI, Cloudflare Worker + D1 for the remote control plane and durable task ledger, and authenticated GitHub/ChatGPT integrations for code review and repository operations. AndroidHarness may be used only for occasional operator-initiated diagnostics.

Do not assume a direct ChatGPT-to-AndroidHarness control channel. A future MCP bridge must be authenticated, workspace-bound, allowlisted, audited, rate-limited, cancellable and protected from arbitrary command execution.

## 6. Priority roadmap

| Priority | Work | Why | Gate |
|---|---|---|---|
| Deferred | AndroidHarness 3.2 setup / device worker | Not required for engine operation | Revisit only if the operator explicitly requests device-specific compatibility work |
| P1 | Cloudflare Workflows deterministic spike | Best fit for durable task execution on current platform | Local/CI only; no providers or deploy |
| P2 | Restart/resume and interrupted-write tests for file-backed checkpoint adapter | Prove persistence before D1 | Fresh adapter instance, HMAC verification, failure injection |
| P3 | Compare Pydantic AI StepPersistence with MAF adapter | Check whether a smaller agent runtime improves reliability | No-provider isolated benchmark |
| P4 | Improve memory retrieval using indexed Markdown/SQLite/D1 | Lower token use and better context selection | Retrieval benchmark before vector DB |
| P5 | Evaluate Qdrant Edge / Letta / Graphiti only against a demonstrated gap | Avoid premature infrastructure | Measured retrieval or temporal-memory need |

## Adoption rule

No framework, agent, plugin, vector database or hosted provider is adopted because it is popular or feature-rich. It must solve a measured limitation, fit zero-spend or have a separately approved budget, preserve policy/quota enforcement, support bounded execution and auditability, have a maintained upstream and clear license, and pass isolated tests before production integration.

## Sources

- [Cloudflare Workflows overview](https://developers.cloudflare.com/workflows/)
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