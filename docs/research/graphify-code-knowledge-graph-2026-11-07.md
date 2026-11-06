# Knowledge graph and agent coordination evaluation — 2026-11-07

**Decision:** PILOT Graphify OSS as a read-only code/config knowledge graph. Do not replace the existing task store, task DAG, worker, leases, or scheduler. GraphClaw and Noriq remain comparison references, not dependencies.

## 1. What the project actually needs

Raawaa has three different graphs that must not be conflated:

1. **Code/dependency graph:** which modules, functions, classes, workflows and configuration files connect. This is the Graphify use case.
2. **Project/agent capability graph:** which declared role owns which capability, component, tool, model route, review duty or fallback. The initial explicit map is `config/project-agent-map.json`; every node and edge carries an honest implementation status and source evidence.
3. **Live work graph:** task dependencies, priorities, execution lanes, leases, retries and claims. This already belongs to the existing SQLite/D1 Task Store and Worker. It is the source of truth for runtime tasks; do not duplicate it in a new graph database.

The missing link is not another orchestration framework. It is a verified bridge between the static code/agent maps and the existing live task graph, plus a real process supervisor, connected policy-enforced tool adapters, independent reviewer runtime, and a complete R&D-to-task lifecycle.

## 2. Graphify OSS

Primary source: https://github.com/Graphify-Labs/graphify (branch `v8`).

- License: Apache-2.0. The upstream `pyproject.toml` currently identifies package `graphifyy` version `0.9.103`.
- Local code parsing uses tree-sitter AST; code-only extraction works without a model key and does not need a vector database or hosted Graphify service.
- It produces `graph.json`, an interactive `graph.html`, and a `GRAPH_REPORT.md`; graph edges distinguish extracted, inferred and ambiguous relationships.
- It can map code and structured configuration locally. Semantic extraction of prose, PDFs and media can require a model; the pilot explicitly uses `--code-only`, no model key and no external inference.
- The upstream project supports MCP serving and agent-facing graph queries, but those integrations are not enabled in this pilot.
- Graphify Cloud's trial/hosted features are excluded: no paid service, no account connection and no data upload.

**Pilot scope:** run the pinned OSS CLI in an isolated GitHub Actions job, with read-only repository permissions, no secrets, no model/provider calls, bounded timeout and a short-lived artifact. Keep the graph outside Git history. The pilot must not gate or cancel the existing discovery job.

**Acceptance:** non-empty graph; schema and evidence paths validate; no absolute/local paths; source files are from the current checkout; code/config map generated with zero model calls; artifact retained for seven days; workflow cost remains $0. Then manually evaluate a fixed set of architecture questions against the graph and raw repository search before calling it useful.

**Rollback:** remove the independent workflow job and ignore file. Existing source search, inventories and task store continue unchanged. No runtime depends on the graph artifact.

## 3. Alternatives

| Project | What it actually does | Why not adopt now |
|---|---|---|
| GraphClaw — https://github.com/graphclaw/graphclaw | Graph-native project/task orchestration, agent loop, state machine, 7-factor task scoring, plugin backends. Its documented default stack includes PostgreSQL + Apache AGE, pgvector, Redis, MinIO, FastAPI and configurable LLM providers. | Duplicates the current D1 task ledger, dependency DAG, leases and priority system while adding a separate database/service stack, model configuration and operational burden. No evidence it improves our existing task contract enough to justify migration. |
| Noriq — https://github.com/noriq-dev/noriq | Agent-first coordination via MCP, Cloudflare Workers, Durable Objects and D1; plans/phases, task claims, expiring leases, comments and mission control. | Architecturally closer to our Cloudflare stack, but it still duplicates the existing coordination plane. Its current README explicitly says repository code indexing is not a product capability and its execution runner was removed; agents execute externally. It is a useful pattern/reference for live agent messaging and claims, not a drop-in replacement or code graph. |
| LangGraph / CrewAI / Agno | General agent orchestration frameworks. | Existing planner/task DAG and Cloudflare/GitHub scheduling already cover parts of this. Prior evaluation found no proven superiority; adding one now creates another state/runtime model before the process supervisor and reviewer gaps are fixed. |

## 4. Initial project/agent topology

`config/project-agent-map.json` is a static, evidence-backed architecture overlay. It intentionally marks the agent roster as `declared_only`, model routes as disabled, Tool Broker adapters as unconnected, R&D proposals as artifact-only, and process supervision / independent runtime reviewer as gaps. It is not proof of live agent execution and does not copy live D1 task records.

`scripts/validate_project_agent_map.py` checks unique node IDs, valid edge endpoints, and existence/safety of evidence paths. It is deterministic and makes no network or model calls.

## 5. R&D cycle completion still required

The existing daily discovery workflow collects metadata, triages candidates and emits deterministic capability proposals. It does not yet:
- verify candidate quality/security/license against primary sources as an automated gated stage;
- compare candidates using reproducible benchmarks against a measured current baseline;
- promote proposals into the existing Task Store with idempotency and evidence;
- run isolated coding agents under a hard process-tree timeout and credential isolation;
- invoke Tool Broker adapters through runtime with durable audit evidence;
- run an independent read-only reviewer;
- promote/rollback a provider or tool through a canary with measurable acceptance;
- retain a versioned decision/adoption/fallback register with tested recovery.

No candidate should be called an adopted replacement until a controlled experiment proves a material improvement, security and license gates pass, and rollback is tested. The previous working implementation remains the default until the replacement passes.

## 6. Sources and verification

- Graphify source repository and Apache-2.0 license: https://github.com/Graphify-Labs/graphify
- Graphify upstream package metadata: `pyproject.toml` on branch `v8` (package `graphifyy==0.9.103` at evaluation).
- Graphify code-only behavior: upstream `tests/test_extract_code_only_cli.py`.
- GraphClaw source and architecture: https://github.com/graphclaw/graphclaw
- Noriq source and runner cut-over note: https://github.com/noriq-dev/noriq

All repository facts above were inspected on 2026-11-07. Recheck upstream versions, licenses, security advisories and project activity before upgrading or promoting the pilot.

## 7. First CI experiment and correction

- First exact-head run: [research workflow #42312019469](https://github.com/ghith93/raawaa-autonomous-engine/actions/runs/42312019469).
- The project-agent map validator passed. Graphify OSS `0.9.103` installed successfully without a model key.
- The first extraction produced **5,297 nodes, 11,848 edges and 256 communities** from 335 code files. Four sensitive/credential-related paths were excluded by ignore rules; one sensitive workflow file was also skipped by Graphify's own safety filter.
- The job then failed because `extract --code-only` writes the graph JSON first and the report/HTML generation requires the documented `--cluster-only` pass. It also reported 18 SQL files skipped because the optional SQL grammar was absent.
- Corrective change on this PR: install the pinned `graphifyy[sql]==0.9.103` extra, run `python -m graphify cluster-only . --no-label` (the CLI subcommand, with community LLM labeling explicitly disabled), and validate graph/report/HTML plus relative source paths before upload.
- This is a genuine first pilot result, not yet a passing end-to-end graph artifact. Re-run exact-head CI after the corrective commit and do not merge until the graph job and full Engine CI pass.

## 8. Existing six-hour development loop and fallback reality

The repository also has `scripts/build_development_state.py` and `engine/development_cycle.py` scheduled by `maintenance.yml` every six hours. They collect a read-only state snapshot and deterministically rank main CI, deploy/watchdog freshness, queue signals, PRs, issues and hygiene. They do not create, claim or execute tasks. Arena's execution round is still on-demand; ChatGPT/council review is on-demand. This is a second useful existing signal source, not a second R&D executor.

The provider inventory is also not an operational fallback system:
- Groq GPT-OSS 20B is registered as a primary candidate but `enabled=false`; its last account/quota verification is dated 2026-11-01.
- Groq GPT-OSS 120B is labelled fallback but `free_verified=false`, `production_use_allowed=false`, `enabled=false` after an HTTP 403 permission denial.
- Groq Qwen is preview-only and disabled.
- NVIDIA Nemotron is a disabled candidate with unverified numeric quota and commercial/production eligibility.
- The registry currently has no enabled eligible route. Therefore 120B is a **named fallback candidate**, not a functioning backup.

Required R&D completion: a versioned capability/provider/tool adoption register with explicit `primary`, `fallback`, `standby`, `rejected`, and `disabled` states; current evidence and expiry; a tested zero-cost eligibility check; per-route kill switch and hard quota cap; canary/benchmark evidence; and a rollback test. A fallback must be exercised in a bounded staging test before it can be called operational. Until then, keep existing behavior and all disabled routes unchanged.

## 9. Successful end-to-end pilot evidence — 2026-11-06/07

Corrected exact-PR-head run: [42312433741](https://github.com/ghith93/raawaa-autonomous-engine/actions/runs/42312433741), PR head `4aec1b7ff428566aa2b3e7f7dc7648972b98880e`.

- The Graphify job completed **successfully** and uploaded artifact `project-knowledge-graph-42312433741` (425,868 bytes, seven-day retention).
- Downloaded and independently inspected the ZIP: `graph.json`, `graph.html`, and `GRAPH_REPORT.md) all exist.
- Actual graph: **5,394 nodes, 11,981 edges, 255 communities**; 92% extracted, 8% inferred, 0% ambiguous; zero model tokens/cost.
- Graph includes 18 SQL migration/schema files (97 SQL nodes) and 313 unique source files across Python, YAML, JavaScript, TypeScript, SQL, JSON and shell.
- Path safety check found no absolute or parent-traversal source paths.
- Graph metadata records the pull-request merge ref `0d5c4087`; the associated PR source head was `4aec1b7...`.
- Full Engine CI run [42312433515](https://github.com/ghith93/raawaa-autonomous-engine/actions/runs/42312433515) passed: Python tests, Cloudflare Worker tests, Workflows tests, local D1 integration smoke test and local Workflows runtime smoke test. Staging migration/deploy/telemetry jobs were explicitly skipped.
- This proves the bounded local graph build and artifact pipeline. It does **not** prove that agents are dynamically connected to this graph, that the graph is a live task orchestrator, or that R&D candidate promotion/fallback is implemented.

**Decision:** Graphify passes the technical pilot acceptance gate and can be retained as a read-only code-topology artifact generator. Keep it non-blocking and independent of runtime. Do not merge to main until the repository owner/operator verifies the downstream main-push Staging workflow side effects are compatible with the active #577 zero-write/deploy boundary.



## 9. Combined code + agent topology graph

The static map is now merged into Graphify output. `scripts/merge_project_agent_map.py` adds agent/component/gap/provider nodes, explicit topology edges, and evidence links to AST file nodes or path-only references for docs excluded by `--code-only`. The merge is idempotent and tested. The resulting graph is one navigable view of code structure plus declared roles and their evidence; it does not claim roles are live agents and does not copy live D1 tasks.
