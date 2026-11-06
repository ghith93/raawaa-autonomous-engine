# Capability adoption and fallback register — 2026-11-07

**Purpose:** one evidence-backed view of what is actually active, what is only a candidate, what was rejected/deferred, and what can serve as a fallback. This is not an enablement instruction. All provider routes remain disabled and all new infrastructure remains unadopted until its stated gate passes.

## 1. Core system — keep as the source of truth

| Capability | Current implementation | Verified status | Decision |
|---|---|---|---|
| Live task ledger, dependencies, execution lanes and leases | SQLite / RemoteTaskStore / Cloudflare Worker + D1 | Implemented; lane-aware claims and dependency handling exist. Exact task-ID claim for all task types remains an open #577 gap. | **Primary. Keep and complete #577.** Do not replace with another task DB. |
| Scheduled development state + priority ranking | `scripts/build_development_state.py`, `engine/development_cycle.py`, `maintenance.yml` | Every six hours; read-only collection and deterministic ranking. No task creation, claim or execution. | **Primary signal source.** Connect ranking to task execution only after process/reviewer controls. |
| Research discovery | `engine/research_discovery.py`, `research-discovery.yml` | Daily bounded GitHub/Crossref/arXiv metadata discovery; no cloning/install/execution. SearXNG optional and not configured. | **Primary discovery pipeline.** Expand sources after quality/terms gates. |
| Candidate triage + capability gap analysis | `scripts/triage_research_candidates.py`, `scripts/analyze_capability_gaps.py` | Deterministic metadata scoring and offline repo-baseline proposals; no automatic task creation or adoption. | **Keep.** Add primary-source verification and benchmark stages. |
| Research memory | GitHub Actions artifacts; optional D1 `research_candidates` persistence | Artifacts retained 30 days; D1 persistence requires the existing opt-in variable. | **Keep existing storage.** No second vector DB. |
| Tool permissions | Tool Broker + `config/tool_permissions.json` | Deny-by-default; read-file/list-files are the only registered tools; no external adapters connected to live agents. | **Keep and connect carefully.** Do not grant tools merely because they are discovered. |
| Agent roster/runtime | `config/agents.json`, `engine/agent_runtime.py` | Role roster is declarative; runtime validates deterministic task contracts but does not invoke models. | **Keep as contract.** Not a live multi-agent team yet. |
| Knowledge graph | Graphify OSS pilot in PR #620 | Initial run created 5,297 nodes / 11,848 edges / 256 communities; report-generation step needed correction. Exact-head corrected CI still pending. | **Pilot only.** Does not become a task orchestrator or source of truth. |

## 2. Agent and orchestration alternatives

| Candidate | What it provides | Decision | Why / fallback |
|---|---|---|---|
| Graphify OSS (Graphify-Labs/graphify, Apache-2.0) | Local tree-sitter code/config knowledge graph, query/path/explain, graph.json/html/report. Code-only mode needs no model. | **PILOT** | Use as code map only. If unavailable, existing repository search and source browsing remain the fallback. |
| GraphClaw (Apache-2.0) | Graph-native task orchestration, scoring and agent loop; default stack uses PostgreSQL + AGE + pgvector + Redis + MinIO + LLM providers. | **NOT ADOPTED** | Duplicates D1 task graph/ledger and adds a separate database/service/model stack. Reconsider only if a measured task-graph gap cannot be fixed in current D1. |
| Noriq (MIT) | Cloudflare DO/D1/MCP agent coordination, plans, claims, live comments and mission control. Current README says repository code indexing is not a product capability and the execution runner was removed. | **REFERENCE ONLY** | Closest coordination pattern, but duplicates current task/claim plane and requires a second deployment/state model. Borrow ideas, not infrastructure. |
| LangGraph / CrewAI / Agno | General multi-agent orchestration/state graphs. | **DEFERRED / NOT ADOPTED** | Existing task DAG, leases, scheduler and workflow planner overlap. No measured superiority demonstrated. |
| Microsoft Agent Framework | Parallel/sequential agent workflows; isolated evaluation prototype exists. | **EVALUATION ONLY** | Fake workflow success does not prove durable D1 state, security or production suitability. |
| OpenHands | Coding-agent server and ACP-compatible agent backends. | **NOT ADOPTED** | Requires a real host/container, isolated process manager, model route and durable operations; none should be assumed free/always-on. |
| Goose / PydanticAI | CLI recipes/MCP or typed agent/tool layer. | **RESEARCH REFERENCES** | Potential adapter patterns only; neither supplies the missing secure process supervisor and complete task lifecycle. |

## 3. Models and provider fallback status

| Route | Intended role | Current evidence/state | Operational status |
|---|---|---|---|
| Groq GPT-OSS 20B | Preferred free candidate | Account/model/quota check last dated 2026-11-01; historical isolated Staging tests exist; route `enabled=false`. Live dispatch guard still needs reconciliation and no default route activation is authorized. | **Disabled candidate — not an active primary.** |
| Groq GPT-OSS 120B | Intended fallback candidate | HTTP 403 model-permission denial; `free_verified=false`, `production_use_allowed=false`, `enabled=false`. Catalog visibility does not prove inference permission. | **Quarantined — not a valid backup.** |
| Groq Qwen 3.8 27B | Evaluation/preview | Preview-only, disabled. | **Evaluation only.** |
| NVIDIA Nemotron 3.5 Lightning | Independent specialist candidate | Numeric quota, account eligibility, hosted terms and commercial/production permission are unverified; disabled. | **Research only.** |
| Cloudflare Workers AI GLM-4.7-Flash | Potential additional free hosted model | Public model/pricing docs reviewed; account eligibility, total account quota, data terms and runtime adapter not verified. | **Research candidate only.** |
| Cloudflare Gemma 4 26B | Workers AI accounting spike | Staging-only Neuron ledger work exists in draft PR #405; no complete model adapter/consumer and no proof it controls all account usage. | **Parked spike, not a backup.** |

**Critical finding:** there is currently **no active eligible model route and no operational model fallback**. The string `fallback` in the GPT-OSS 120B route ID is a desired role, not proof that it can serve requests. Keep paid fallback disabled and USD spend at zero. A model only becomes a backup after fresh eligibility/quota verification, an isolated canary, hard-stop proof, quality comparison, and a successful failover + rollback test.

## 4. Research and security source status

| Source/tool | Status | Use |
|---|---|---|
| GitHub repository metadata | Active | Candidate discovery only; stars/activity are weak quality signals. |
| Crossref + arXiv metadata | Active, bounded | Research-paper discovery; no automatic full-text ingestion or licensing conclusion. |
| SearXNG | Adapter present, endpoint absent | Not active until a trusted HTTPS endpoint is configured. |
| MCP Registry / OSV / OpenSSF Scorecard / Hugging Face | Proposed source expansion | Add bounded metadata checks and primary-source verification; not yet an integrated pipeline. |
| SkillSpector | Evaluated, not installed | Keep as a candidate for static skill screening; require sandbox and independent review before any imported skill executes. |
| Mem0 / Letta / Zep / Graphiti / Qdrant | Deferred | No second memory/vector/graph database until retrieval-quality benchmarks show a real gap. |

## 5. The actual missing capabilities

1. **R&D promotion loop:** primary-source verification → comparable baseline → reproducible benchmark → decision record → task in existing Task Store → isolated implementation → independent review → canary → adopt/rollback.
2. **Process supervisor:** spawn and supervise coding-agent CLI processes, hard timeout and process-tree reap, lease fencing, cancellation, restart and credential isolation.
3. **Independent reviewer runtime:** read-only separate identity, no push token, structured verdict and evidence.
4. **Tool Broker adapters:** MCP/tools must route through policy, role/task/environment allowlists, schema validation, audit and idempotency.
5. **Live agent bridge:** declared role topology must become actual claimable task roles with acknowledgments, durable handoff and observable liveness.
6. **Operational fallback register:** primary/standby/disabled/quarantined/rejected states, verification expiry, quotas, health, failover tests and rollback evidence.

## 6. Adoption rules

- Preserve the working implementation until the replacement passes measurable tests.
- Do not remove a working component just because a newer candidate exists. Retain it as standby only if its license, security, cost, maintenance and rollback are acceptable.
- Never label a candidate as a backup until a failover test proves it works.
- Keep one source of truth per concern: D1 for live tasks, provider registry/quota ledger for model routes, research records for candidates, Graphify artifact for code topology.
- No paid calls, paid services, account/secret/permission changes, Production migrations/deployments, or customer-facing actions under the current USD 0 policy.
- Every decision must cite a primary source, verification date, exact version/commit, acceptance test, and rollback.

## 7. Related work

- Permanent R&D operating system: `docs/research-and-development-operating-system.ar.md` (PR #408 merged).
- Detailed 2026-11-04 R&D cycle report: PR #523 remains open/draft with a stale base; do not merge wholesale.
- Six-hour read-only development loop: `docs/operations/permanent-development-loop.md`.
- Multi-CLI phase status: `docs/operations/multi-cli-execution-plan.md`.
- Graphify pilot and alternatives: `docs/research/graphify-code-knowledge-graph-2026-11-07.md`.
