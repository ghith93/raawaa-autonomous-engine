# Microsoft Agent Framework — isolated evaluation (2026-11-02)

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

## Observed result

The isolated evaluation passed on exact PR head [run 41352457283](https://github.com/ghith93/raawaa-autonomous-engine/actions/runs/41352457283):
- Two research branches executed in parallel and fed a reviewer step.
- Checkpoint replay returned the same output without re-running any completed step.
- The OpenAI Chat Completions client exposes the base_url parameter needed to target Groq-compatible endpoints.
- Provider requests sent: 0. Paid services used: 0.

## Checkpoint persistence boundary prototype

The isolated experiment adds `experiments/microsoft_agent_framework/checkpoint_envelope.py` and unit tests.

The prototype defines a deterministic JSON transport envelope with:
- Explicit schema version, workflow/checkpoint identifiers, parent lineage, and sequence metadata.
- A 256 KiB encoded-envelope limit, maximum JSON nesting depth of 64, and maximum 20,000 payload values.
- Canonical UTF-8 JSON encoding, duplicate-key rejection, and finite-number validation.
- SHA-256 checksum verification to detect accidental corruption or an un-recomputed payload change.

**Security limitation:** SHA-256 here is an unkeyed checksum, not authentication. Anyone able to rewrite the stored payload can also recompute the checksum. It does not prevent a malicious storage writer from forging a checkpoint. A production adapter must add authenticated integrity (for example, a keyed MAC with separately managed secret material or a trusted authenticated storage boundary), plus authorization and lineage checks.

**Serialization limitation:** this is not a Microsoft Agent Framework serializer and does not prove that nested executor state can be represented as JSON. The framework's supported checkpoint encoder/decoder must produce and consume a JSON-safe payload before this envelope can be used. Do not serialize framework objects with raw pickle, and do not deserialize untrusted pickle content.

The prototype is not connected to D1, does not create a migration, does not call the Worker, and does not perform a production write. It is a contract test only. D1 storage still requires authenticated bounded CRUD, per-workflow lineage/order enforcement, tamper rejection, restart/resume tests, retention/cleanup policy, and explicit production approval.

## Next evaluation gates

1. Verify the framework-supported checkpoint encoding API against the pinned package versions; do not infer compatibility from WorkflowCheckpoint.to_dict() alone.
2. Build a fake/in-memory adapter against the exact framework CheckpointStorage protocol and run save/load/latest/list/delete/restart tests.
3. Prove malformed, oversized, tampered, cross-workflow, stale-sequence, and replayed records are rejected, including authenticated integrity.
4. Verify every agent/provider execution path reuses engine.operating_policy.load_operating_policy() and the existing quota/data-classification gateway.
5. Only then design a D1 adapter. Any migration/deployment remains behind the existing production approval gate.

## Decision and safety

Do not replace the current runtime or add these dependencies to default engine requirements yet. The experiment remains isolated and optional. The evaluation sends zero provider requests, uses zero paid services, requires no credentials, and does not change production data or deployment state.

## References

- https://github.com/microsoft/agent-framework/blob/main/LICENSE
- https://github.com/microsoft/agent-framework/blob/main/SECURITY.md
- https://github.com/microsoft/agent-framework/blob/main/python/packages/core/pyproject.toml
- https://github.com/microsoft/agent-framework/blob/main/python/packages/openai/pyproject.toml
- https://learn.microsoft.com/agent-framework/integrations/openai-endpoints
- https://github.com/microsoft/agent-framework/releases/tag/python-1.24.0
