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

