# Architecture and staged design

## Current implementation

The repository currently contains a Python core, JSON configuration, policy evaluator, task/memory stores, deterministic workflow planner, quota-aware provider routing, opt-in live-runtime wiring, data-classification gate, CLI, unit tests, and GitHub Actions CI. A bounded daily GitHub repository discovery workflow is implemented as the first proactive research source; its output is unreviewed metadata, not an autonomous research/recommendation agent. The roster and planner remain declarative/deterministic rather than a persistent multi-agent runtime.

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

## Runtime operating-policy enforcement

The Python worker calls `engine.operating_policy.load_operating_policy()` at the beginning of every `run_once` cycle, before claiming a task. The loader reads and validates `AGENTS.md`, the permanent master directive, continuous-improvement charter, architecture, and operations guide, computes SHA-256 fingerprints, and fails closed if any required document is missing, unreadable, empty, or invalid. For live model requests, a compact trusted policy summary and the fingerprints are inserted before untrusted task payload; full source documents are read locally rather than copied wholesale into the prompt, preserving existing provider quota limits. This is enforced for the Python worker and CLI Task Runner path only. Any additional agent/provider execution path must reuse this loader and prove integration with tests.

## Cloud execution and device independence

The Cloudflare control Worker + D1 task ledger is the remote control plane and durable source of truth. The GitHub Actions Task Runner is a bounded, ephemeral consumer (five tasks / 60 seconds per run) scheduled every 5 minutes. Within one invocation, the Python batch runner may execute up to the policy's `max_parallel_agents` limit (currently 2); each worker claims a distinct fenced D1 task lease and shares the atomic provider quota ledger. This improves batch throughput without adding a scheduler, queue, or independent state store. GitHub documents that scheduled runs can be delayed or dropped under load and public-repository schedules can be disabled after 60 days without activity. It is not a persistent process or a 24/7 guarantee.

AndroidHarness is an optional operator-side diagnostic and compatibility environment only. No task, CI, recovery, or core engine capability may require the tablet poller or WorkManager. Device unavailability must never block cloud queue progress.

Durable Objects SQLite + Alarms are not an immediate replacement for the current runner. The existing Worker Cron already performs stale-lease recovery, while actual task handlers currently execute in the Python GitHub Actions runner; an alarm cannot execute that Python code by itself. A Durable Object would add little value unless the handlers are deliberately ported into the Worker or an existing safe remote runner is provided. Defer adoption; if reconsidered, use it only as a wake-up, retain D1 as the canonical task ledger, and prove restart recovery, single-consumer safety, idempotency, and zero-cost operation before adding a binding. Cloudflare free-tier limits are hard failure thresholds, not permission to incur charges.

References: [GitHub scheduled workflow caveats](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows), [Durable Object alarms](https://developers.cloudflare.com/durable-objects/api/alarms/), [Durable Objects pricing](https://developers.cloudflare.com/durable-objects/platform/pricing/).

## Model/provider integration

Provider profiles are now staged in `config/provider_profiles.json` but all remain disabled. Groq GPT-OSS 20B/120B are production-model candidates; Qwen 3.8-27B is preview-only. The read-only profile registry does not load secret values or enable network calls. Future live adapters must read credentials only from environment variables or secret stores, never from committed files. The router enforces per-minute, per-task, daily, and monthly request/token quotas before making a request and records usage after it returns. Paid routes additionally require a monetary ledger and explicit approval.

## Durable provider quota ledger

Cloud live inference also requires the authenticated D1 monetary usage ledger. It reserves and settles integer nano-USD in D1; local SQLite remains for local development only. Migration 0005 and the `/v1/usage` Worker route must be reviewed, deployed, and restart/concurrency-tested before cloud live inference can be enabled. Missing D1 support fails closed without local fallback.


Provider request/token usage, reservations, cooldowns, and metadata-only audit events are stored in D1 through the authenticated Worker route `/v1/quota`. The Python `D1QuotaLedger` uses only this narrow action API; it does not expose arbitrary SQL. A reservation is a single conditional `INSERT ... SELECT ... WHERE ... RETURNING` statement, so the quota predicates and reservation insert share one database write statement. Identical settlement retries are accepted; conflicting settlements return a conflict. A transport failure is treated as ambiguous and raises without local-ledger fallback.

Cloud/scheduled execution selects this ledger whenever `RAAWAA_ENGINE_API_URL` is configured. It requires `RAAWAA_ENGINE_API_TOKEN` and, when Cloudflare Access protects the Worker, the paired `RAAWAA_ENGINE_CF_ACCESS_CLIENT_ID` and `RAAWAA_ENGINE_CF_ACCESS_CLIENT_SECRET` secrets. Missing credentials or Worker errors fail closed. Local development and unit tests may continue to use SQLite when no Worker URL is configured.

The D1 schema is additive in `migrations/0003_durable_quota_ledger.sql`; it does not copy or migrate historical rows from runner-local SQLite. Therefore prior local usage cannot be reconstructed centrally and must not be treated as available capacity. The live inference kill switch remains off until CI verifies the Worker routes and Python client and a real D1 concurrency/restart test has been run against a non-production test database. The monetary ledger remains a separate SQLite subsystem; current routing permits only verified zero-cost profiles and paid calls remain disabled.

## Optional AndroidHarness diagnostics only

AndroidHarness 3.1 is retained only as an optional operator-side diagnostic and compatibility environment. It is not an execution backend, task consumer, scheduler, recovery worker, or release gate. The GitHub issue bridge may be used for occasional device-specific diagnostics when an operator explicitly starts it, but normal project work, CI, cloud task execution, and recovery must not depend on a device session, foreground poller, WorkManager, or harness model quota.

Do not configure recurring device prompts, WorkManager automation, or a foreground poller as part of the engine. Do not upgrade the installed app or request additional device permissions for normal operation. Keep the issue bridge allowlisted and inactive by default. See [AndroidHarness runtime profile](android-harness.md) for the historical integration details and current restrictions.

## Device independence and scheduler boundaries

- Cloudflare Worker + D1 remain the remote control plane and durable task ledger.
- GitHub Actions is the bounded Python consumer, scheduled every five minutes (five tasks / 60 seconds per batch). Scheduled execution can be delayed or dropped; this is not a 24/7 guarantee.
- Cloudflare Worker Cron independently performs stale-lease recovery every fifteen minutes. This maintenance trigger does not execute Python task handlers.
- Durable Objects Alarms are deferred: they cannot run the current Python handlers, and Worker Cron already performs stale-lease recovery. Do not add a second scheduler or queue until a Worker-native executor is deliberately implemented and measured.
- D1 remains the canonical task ledger. No paid hosting, inference, or account upgrade is authorized by this architecture.
