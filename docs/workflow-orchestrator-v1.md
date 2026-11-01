# Workflow Orchestrator v1

## Scope
This release adds a deterministic, fail-closed DAG validator and planner. It does not execute agents, call model APIs, invoke tools, or write to external services.

## Workflow contract
Each workflow has a stable `id` and a non-empty `steps` array. Each step declares:
- stable `id`
- allowlisted `task_type`
- non-empty `title`
- enabled roster `agent`
- optional `depends_on` step IDs

The planner rejects duplicate IDs, unknown or disabled agents, unknown dependencies, duplicate/self dependencies, unsupported task types, malformed contracts, and dependency cycles. Output is deterministic and identifies root steps that are ready now and steps waiting on prerequisites.

## Safety and limitations
- Planning only: no task is enqueued and no worker is started.
- No provider/network calls; no secrets are read.
- No automatic retry, timeout, reviewer approval, or durable workflow-run state yet.
- A future execution layer must persist workflow and step states, enforce dependency completion transactionally, apply max-attempt/timeout limits, and require review before consequential actions.

## Provider discovery strategy
The provider catalog is metadata only. Prefer runtime model discovery endpoints where supported, rather than stale hard-coded model names. Candidate gateways (OmniRoute, LiteLLM, New API) remain evaluation targets, not runtime dependencies. Free-tier limits and model availability must be revalidated before activation.
