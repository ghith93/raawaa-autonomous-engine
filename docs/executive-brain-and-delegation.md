# Executive Brain and Delegation Contract

## Who is the brain?

The brain is a layered control system, not one permanently running chatbot and not a single vendor model.

1. **Executive Control Plane (deterministic authority):** owns the canonical operating policy, objective registry, task ledger, priorities, budget limits, permissions, approval gates, and decision/audit history. It is the final authority on what may be scheduled.
2. **Planning Intelligence (model-assisted, proposal-only):** a future verified model route may interpret goals, inspect evidence, draft plans, identify dependencies, estimate cost/risk, and explain alternatives. It cannot directly enqueue work, grant permissions, change budgets, or approve its own proposal.
3. **Orchestrator (deterministic validator):** validates the proposed workflow DAG, checks task-type allowlists, assigns the required specialist from the trusted task contract, verifies capabilities, checks dependencies, cost ceilings, parallelism, and review requirements.
4. **Specialist workers:** execute only the task type and capability contract assigned to them, using scoped tools and a fenced task lease.
5. **Independent reviewer:** verifies consequential work against acceptance checks and evidence. A worker cannot self-approve its own consequential change.

The manager entry in config/agents.json is a declared orchestration role, not a live model process. It must not be described as an always-on agent until a durable runtime and actual model/tool invocation have been proven.

## How delegation works

### Step 1 — Intake and classification

An objective enters with an owner/source, expected outcome, success metric, deadline or review date, data classification, and risk category. Untrusted task text is input, never policy.

### Step 2 — Planning proposal

Planning Intelligence may draft a JSON workflow with 1–20 bounded steps. Every step must specify:
- stable step ID and allowlisted task type;
- title and structured payload;
- acceptance checks that can be verified;
- dependencies;
- risk level;
- estimated model cost.

The model's output is a proposal, not a command.

### Step 3 — Deterministic assignment

engine.agent_runtime.TASK_AGENT_CONTRACTS is the trusted assignment map:

| Task type | Assigned specialist | Required capability |
|---|---|---|
| research_brief | researcher | research |
| code_review | reviewer | review |
| content_draft | marketer | draft_content |
| system_audit | operator | monitor |
| mobile_test | mobile_tester | android_ui_testing (disabled; not in worker allowlist) |

The proposer cannot select a different enabled agent to bypass the map. The roster must contain the mapped agent, it must be enabled, and it must have the required capability. The manager plans and coordinates; it is not a substitute executor for specialist tasks.

### Step 4 — DAG and dispatch readiness

The orchestrator rejects duplicate IDs, unknown dependencies, cycles, unsafe task types, disabled/missing specialists, capability mismatches, missing acceptance checks, malformed estimates, and per-task budget violations. It computes a deterministic topological order and execution waves. Independent tasks may share a wave, capped by max_parallel_agents; dependent work waits for upstream success.

**Important:** this stage currently validates and previews the DAG only. The existing SQLite/D1 task queue does not yet enforce workflow dependency state at claim time. Do not enqueue a multi-step dependent workflow as if dependencies were enforced until a dependency-aware queue contract is implemented and tested in both SQLite and D1.

### Step 5 — Execution and verification

After the dependency-aware dispatcher is implemented, it may enqueue only currently eligible root/ready steps using stable idempotency keys. Existing workers claim tasks using fenced leases. The scheduler may run no more than the policy concurrency limit. A task becomes eligible for downstream work only after a successful terminal state is observed from the canonical ledger.

For consequential work, the reviewer role must be separate from the executor and must return evidence against the acceptance checks. Failed or ambiguous outcomes block dependent tasks and go to triage; they are not silently treated as success.

### Step 6 — Learning and reprioritization

Record the plan version, rationale, assigned role, selected model/profile, estimated and actual cost, duration, attempts, outcome, reviewer result, and evidence references. Use measured quality, reliability, latency, and cost to improve future assignment. Never change permission policy or budget ceilings through self-learning.

## Model strategy

- The planner model is selected by the existing provider registry/router only after provider terms, zero-cost status, context limits, rate limits, commercial use, credentials, and quota enforcement are verified.
- Prefer the best verified free route for the specific planning task; do not hard-code Gemini, Grok, or any vendor as the permanent brain.
- Use a smaller deterministic route for classification and validation; use a stronger eligible model for strategic decomposition and difficult research only when available within the zero-cost envelope.
- If no eligible model is available, fail closed to deterministic triage and leave the plan pending; do not silently fall back to a paid model.
- The current configuration is safe_dry_run; provider profiles are disabled and paid inference is disabled. Therefore no LLM currently performs live planning or specialist inference.

## Current implementation and gaps

Implemented:
- Existing task-to-specialist mapping and capability checks in engine/agent_runtime.py.
- Fenced worker leases and bounded parallel batch execution in engine/worker.py and engine/batch_runner.py.
- Deterministic workflow DAG validation and specialist assignment in engine/orchestrator.py.
- Proposal-only CLI: python -m engine.cli workflow-plan --file path/to/workflow.json.
- Executive read-only portfolio snapshot in engine/executive_intelligence.py.
- Existing quota/budget, data classification, deny-by-default tool permissions, and policy loading.

Not yet implemented:
- A persistent executive cycle that creates and reviews objectives on a schedule.
- A live, verified free planning-model adapter.
- Dependency-aware queue claims and downstream release in both SQLite and D1.
- Durable plan/decision version storage and cross-run orchestration state.
- Independent reviewer enforcement as a required completion gate for consequential work.
- A measured agent/model capability registry based on completed task outcomes.

## Operating boundaries

- Reuse the existing D1 task ledger, worker, scheduler, memory, quota ledger, and model router. Do not add a second queue or database.
- Current max_parallel_agents=2 is the ceiling; no plan can raise it.
- Current provider profiles are disabled, paid model calls are disabled, and safe_dry_run remains the default.
- External writes, publication, messaging, spending, price changes, production deploys, data deletion, access changes, and storefront changes remain approval-gated.
- Do not claim 24/7 operation. GitHub Actions is a bounded scheduled runner, not a persistent daemon.
- Google ARTEMIS/mobile testing remains disabled until its host, device, endpoint, policy and model gates are independently verified.
