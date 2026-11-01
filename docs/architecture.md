# Architecture and staged design

## Current implementation

The repository currently contains a small Python core, JSON configuration, policy evaluator, CLI audit, unit tests, and GitHub Actions CI. This is intentionally not yet an autonomous agent runtime.

## Target architecture

1. **Control plane** — task intake, prioritization, approvals, audit log, budgets, and operator dashboard.
2. **Orchestrator** — decomposes work, assigns specialist roles, tracks dependencies, and verifies completion.
3. **Agent workers** — research, engineering, quality/security, marketing/content, operations, and project-specific specialists.
4. **Model router** — chooses a model by task quality, availability, context window, latency, and cost; free routes first; hard spend caps.
5. **Memory** — structured project facts, task history, decisions, and searchable knowledge with provenance and retention controls.
6. **Tool adapters** — GitHub, web research, design/content tools, analytics, and messaging, each with scoped permissions.
7. **Observability** — structured logs, task states, retries, health checks, cost accounting, and failure alerts.

## Execution lifecycle

`intake -> classify -> plan -> approve (if needed) -> execute -> verify -> record -> report`

Every task should have an owner, objective, bounded scope, deadline, retry limit, estimated cost, allowed tools, and acceptance checks. Retries must be idempotent where possible. The reviewer must not be the same logical role as the worker for consequential changes.

## GitHub Actions versus a persistent worker

GitHub Actions is used first for CI and bounded scheduled jobs. It is event-driven and ephemeral: it does not provide a continuously running process or a durable task queue. A persistent worker and database are later phases, and must not be deployed until the hosting plan, free-tier limits, data persistence, and billing risks are verified.

## Model/provider integration

Provider profiles are now staged in `config/provider_profiles.json` but all remain disabled. Groq GPT-OSS 20B/120B are production-model candidates; Qwen 3.8-27B is preview-only. The read-only profile registry does not load secret values or enable network calls. Future live adapters must read credentials only from environment variables or secret stores, never from committed files. The router enforces per-minute, per-task, daily, and monthly request/token quotas before making a request and records usage after it returns. Paid routes additionally require a monetary ledger and explicit approval.

## Safety boundaries

Human approval is mandatory for publishing, sending messages, spending money, price changes, production deployment, deleting data, changing access, and modifying a storefront. Read-only research and local tests can be automated within configured limits.
