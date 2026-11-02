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

The Cloudflare control Worker + D1 task ledger is the remote control plane and durable source of truth. The GitHub Actions Task Runner is a bounded, ephemeral consumer (five tasks / 60 seconds per run) scheduled every 15 minutes; it is useful for low-cost unattended batches, but GitHub documents that scheduled runs can be delayed or dropped under load and public-repository schedules can be disabled after 60 days without activity. It is not a persistent process or a 24/7 guarantee.

AndroidHarness is an optional operator-side diagnostic and compatibility environment only. No task, CI, recovery, or core engine capability may require the tablet poller or WorkManager. Device unavailability must never block cloud queue progress.

Cloudflare Durable Objects SQLite + Alarms are a candidate for a future cloud-native wake-up mechanism: alarms are durable, at-least-once, and retried with backoff. Evaluate them as a scheduler/wakeup only, retaining D1 as the canonical task ledger and reusing the existing claim/lease/fencing API. Do not create a second queue, duplicate task store, or production Durable Object binding until an isolated test proves restart recovery, single-consumer safety, D1 error handling, idempotency, and zero-cost operation. Cloudflare free-tier limits are hard failure thresholds, not permission to incur charges.

References: [GitHub scheduled workflow caveats](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows), [Durable Object alarms](https://developers.cloudflare.com/durable-objects/api/alarms/), [Durable Objects pricing](https://developers.cloudflare.com/durable-objects/platform/pricing/).

## Model/provider integration

Provider profiles are now staged in `config/provider_profiles.json` but all remain disabled. Groq GPT-OSS 20B/120B are production-model candidates; Qwen 3.8-27B is preview-only. The read-only profile registry does not load secret values or enable network calls. Future live adapters must read credentials only from environment variables or secret stores, never from committed files. The router enforces per-minute, per-task, daily, and monthly request/token quotas before making a request and records usage after it returns. Paid routes additionally require a monetary ledger and explicit approval.

## Durable provider quota ledger

Provider request/token usage, reservations, cooldowns, and metadata-only audit events are stored in D1 through the authenticated Worker route `/v1/quota`. The Python `D1QuotaLedger` uses only this narrow action API; it does not expose arbitrary SQL. A reservation is a single conditional `INSERT ... SELECT ... WHERE ... RETURNING` statement, so the quota predicates and reservation insert share one database write statement. Identical settlement retries are accepted; conflicting settlements return a conflict. A transport failure is treated as ambiguous and raises without local-ledger fallback.

Cloud/scheduled execution selects this ledger whenever `RAAWAA_ENGINE_API_URL` is configured. It requires `RAAWAA_ENGINE_API_TOKEN` and, when Cloudflare Access protects the Worker, the paired `RAAWAA_ENGINE_CF_ACCESS_CLIENT_ID` and `RAAWAA_ENGINE_CF_ACCESS_CLIENT_SECRET` secrets. Missing credentials or Worker errors fail closed. Local development and unit tests may continue to use SQLite when no Worker URL is configured.

The D1 schema is additive in `migrations/0003_durable_quota_ledger.sql`; it does not copy or migrate historical rows from runner-local SQLite. Therefore prior local usage cannot be reconstructed centrally and must not be treated as available capacity. The live inference kill switch remains off until CI verifies the Worker routes and Python client and a real D1 concurrency/restart test has been run against a non-production test database. The monetary ledger remains a separate SQLite subsystem; current routing permits only verified zero-cost profiles and paid calls remain disabled.

## Optional AndroidHarness execution backend

AndroidHarness is registered as an external Android/Linux runtime in `config/runtime_backends.json`. Its intended role is a bounded compatibility lab and optional execution target for Python/Node tools, local-model experiments, agent-framework evaluation, and device-specific validation. It is complementary to GitHub Actions (reproducible CI) and Cloudflare (deployed control plane), not a replacement for either.

The integration now includes an AndroidHarness 3.2 project configuration (`harness.json`), scoped nested-agent profiles, a project operating skill, a read-only environment doctor, and a GitHub issue polling bridge for four fixed read-only operations. The issue bridge is implemented but inactive until a local AndroidHarness poller is started; it is not a direct MCP server or general remote tool adapter. The engine must not claim device execution until a task result is posted by the authenticated local poller. Other device operations still require a real authenticated harness session and a separately tested adapter. The profile records an operator-reported readiness snapshot; all device capabilities must be re-probed before use. The v3.2 SDK installer can prepare Android build tooling behind one explicit approval, but that tooling is optional for this Python-first engine. LiteLLM, llama.cpp/GGUF and local LLM inference remain candidates requiring individual compatibility and resource tests. See [AndroidHarness runtime profile](android-harness.md) and [3.2 evaluation](research/androidharness-3.2-evaluation.md).

## Safety boundaries

Human approval is mandatory for publishing, sending messages, spending money, price changes, production deployment, deleting data, changing access, and modifying a storefront. Read-only research and local tests can be automated within configured limits.


## Recurring Android worker and durability boundary

The preferred tablet-side recurring execution mode is AndroidHarness native scheduled-message automation through Android WorkManager, with a dedicated Code conversation and bounded hourly cycles. See docs/androidharness-workmanager-automation.md for setup, recurring prompt, limits, and recovery. This avoids requiring a manually maintained infinite terminal poller.

WorkManager is an opportunistic device scheduler, not an always-on service or real-time SLA. Cloudflare D1 remains the durable task ledger; GitHub Actions remains CI and the bounded cloud scheduler. AndroidHarness may execute one bounded unit of work per occurrence when the device, app, network, and model quota permit. A missed device run must leave tasks queued, not lose them. Activation requires an actual successful scheduled run and a verified next occurrence in Automation history; repository-side configuration is not device-side proof.
