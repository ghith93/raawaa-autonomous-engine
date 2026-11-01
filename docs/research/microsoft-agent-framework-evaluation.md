# Microsoft Agent Framework — isolated evaluation (2026-11-01)

## Candidate

- Repository: https://github.com/microsoft/agent-framework
- Licence: MIT (repository LICENSE and package metadata).
- Python packages: agent-framework-core 1.24.0, agent-framework-openai 1.18.0.
- Release cadence: Python 1.24.0 was released 2026-10-30; preceding Python 1.23.0 was released 2026-10-22.
- Security reporting: Microsoft Security Response Center; vulnerabilities are not requested through public issues.
- Official docs: https://learn.microsoft.com/agent-framework/integrations/openai-endpoints

## Fit for this engine

Strengths:
- Native workflow/executor abstractions, fan-out/fan-in, functional workflows, and parallel branches.
- Per-step checkpointing and replay that can skip already completed steps after resume.
- Python 3.12 support and an OpenAI Chat Completions client that accepts base_url for OpenAI-compatible services such as Groq.
- MIT licence and a modular package layout: core, OpenAI integration, and optional orchestration packages.

Constraints:
- The built-in InMemoryCheckpointStorage is not durable across ephemeral GitHub Actions/Worker restarts. Durable production use requires a custom storage adapter, likely backed by the existing D1 database.
- The OpenAIChatCompletionClient can call Groq directly when configured with base_url. Direct use would bypass this engine’s QuotaAwareLiveGateway, minute/day/task limits, live-inference kill switch, data-classification gate, and audit ledger. It must not be wired directly into production.
- The optional Durable Task integration is not part of this evaluation and may require an external service; no new hosting or paid service is approved.
- Adopting the framework wholesale would overlap with the existing task queue, deterministic agent contracts, policy gates, and Worker/D1 scheduler.

## Isolated test

The CI evaluation installs only pinned core/OpenAI packages and runs a deterministic workflow with two parallel research branches, a reviewer step, and checkpoint replay. It sends zero provider requests and uses no credentials. It also verifies that the OpenAI Chat Completions client exposes base_url, without constructing a live client or sending a request.

## Decision

Do not replace the current runtime or add these dependencies to the default engine requirements yet. The experiment is isolated and optional. If it passes, the next useful prototype is a D1-backed checkpoint-storage adapter plus a QuotaAwareLiveGateway-compatible client adapter. Only after both work under the existing policy/data gates should we consider integrating the framework as an orchestration layer.

## References

- https://github.com/microsoft/agent-framework/blob/main/LICENSE
- https://github.com/microsoft/agent-framework/blob/main/SECURITY.md
- https://github.com/microsoft/agent-framework/blob/main/python/packages/core/pyproject.toml
- https://github.com/microsoft/agent-framework/blob/main/python/packages/openai/pyproject.toml
- https://learn.microsoft.com/agent-framework/integrations/openai-endpoints
- https://github.com/microsoft/agent-framework/releases/tag/python-1.24.0
