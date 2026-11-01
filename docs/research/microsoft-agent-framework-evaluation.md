# Microsoft Agent Framework — isolated evaluation (2026-11-02)

## Candidate and current decision

- Repository: https://github.com/microsoft/agent-framework
- The pinned optional evaluation currently exercises parallel workflow branches, a reviewer step, and checkpoint replay without provider calls.
- Do not replace the existing task queue, specialist contract, policy gates, or Worker/D1 scheduler.
- Do not add the framework to default runtime dependencies until a supported checkpoint codec, durable storage adapter, and QuotaAwareLiveGateway-compatible model adapter are independently verified.

## Checkpoint persistence boundary prototype

The isolated experiment now includes `experiments/microsoft_agent_framework/checkpoint_envelope.py` and unit tests.

The prototype defines a deterministic JSON transport envelope with:

- Explicit schema version, workflow/checkpoint identifiers, parent lineage, and monotonic sequence metadata.
- A 256 KiB encoded-envelope limit.
- Canonical UTF-8 JSON encoding, duplicate-key rejection, finite-number validation, and SHA-256 integrity verification.
- Strict shape and identifier validation; unknown schema versions and non-canonical encodings fail closed.

**Important limitation:** this is not a Microsoft Agent Framework serializer and does not prove that its nested executor state can be represented as JSON. The framework's supported checkpoint encoder/decoder must produce and consume a JSON-safe payload before this envelope can be used. Do not serialize framework objects with raw pickle, and do not deserialize untrusted pickle content.

The prototype is not connected to D1, does not create a migration, does not call the Worker, and does not perform any production write. It is a contract test only. D1 storage still requires authenticated bounded CRUD, per-workflow lineage/order enforcement, tamper rejection, restart/resume tests, retention/cleanup policy, and explicit production approval.

## Next evaluation gates

1. Verify the framework-supported checkpoint encoding API against the pinned package versions; do not infer compatibility from `WorkflowCheckpoint.to_dict()` alone.
2. Build a fake/in-memory adapter against the exact framework `CheckpointStorage` protocol and run save/load/latest/list/delete/restart tests.
3. Prove malformed, oversized, tampered, cross-workflow, stale-sequence, and replayed records are rejected.
4. Verify every agent/provider execution path reuses `engine.operating_policy.load_operating_policy()` and the existing quota/data-classification gateway.
5. Only then design a D1 adapter. Any migration/deployment remains behind the existing production approval gate.

## Safety

The evaluation sends zero provider requests, uses zero paid services, requires no credentials, and does not change production data or deployment state.
