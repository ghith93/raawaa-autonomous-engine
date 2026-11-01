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

## Pinned Python checkpoint codec and in-memory adapter evaluation (2026-11-02)

Inspection of the exact `python-1.24.0` source confirms:

- `WorkflowCheckpoint.to_dict()` is explicitly shallow; nested event/message/state values are not recursively converted.
- The supported internal storage boundary is `_encode_checkpoint_for_storage()`, which calls `encode_checkpoint_value()` and immediately validates the result with `decode_checkpoint_value(..., allowed_types=...)` before persistence.
- `encode_checkpoint_value()` produces JSON-serializable data but pickles non-JSON-native values into base64 marker strings. The matching decoder uses a restricted allowlist by default when an explicit `allowed_types` set is supplied.
- The framework explicitly treats checkpoint storage as trusted data and warns that its restricted unpickler is defense in depth, not a security boundary. A custom storage must not accept attacker-controlled checkpoint blobs.

The branch now adds `InMemoryEnvelopeCheckpointStorage`, an ephemeral adapter implementing the six `CheckpointStorage` operations against in-memory bytes. It uses the pinned framework codec, an explicit restricted type allowlist, the bounded envelope, and workflow lineage/sequence checks. Its tests cover datetime state round-trip, ordered listing/latest selection, delete, workflow isolation, duplicate IDs, tampering, and rejection of unapproved application-defined types.

This remains an isolated test adapter, not durable storage. The adapter imports private framework modules and is valid only for the pinned package versions; any version change requires a fresh source/API review. It stores pickle-bearing encoded values in memory only. Do not connect it to D1 or any externally writable store. The SHA-256 envelope checksum remains unauthenticated and cannot protect against a malicious writer who can recompute it.

## End-to-end workflow resume using the custom adapter

The deterministic two-branch research workflow now uses `InMemoryEnvelopeCheckpointStorage` instead of the framework's built-in in-memory storage. The pinned framework writes its real workflow checkpoints through the custom adapter; the evaluation retrieves the latest checkpoint and resumes the workflow by checkpoint ID.

Exact PR-head isolated evaluation [run 41358422590](https://github.com/ghith93/raawaa-autonomous-engine/actions/runs/41358422590) passed:
- Both parallel source steps and the reviewer ran exactly once on the first pass.
- After resume, the call counts remained source_a=1, source_b=1, review=1; completed work was not replayed.
- The adapter round-trip and all six storage contract tests passed.
- Provider requests sent: 0. Paid services used: 0.

This proves the pinned framework's current functional workflow can checkpoint and resume through the isolated adapter. It does not prove durability across process restart, D1 compatibility, concurrent distributed writes, or authenticated storage.

## Authenticated checkpoint envelope prototype (2026-11-02)

The envelope now has an authenticated variant using HMAC-SHA256 with domain separation. It requires a key of at least 32 bytes supplied out-of-band; the key is never embedded in the record. The decoder verifies the HMAC in constant time before returning checkpoint payload data. Tests cover correct-key round-trip, wrong-key rejection, short/missing keys, and a storage writer who changes checkpoint content and recomputes the unkeyed SHA-256 checksum but cannot produce a valid HMAC.

The in-memory framework adapter now requires an integrity key. The isolated workflow evaluation generates a fresh random 32-byte key per run; unit tests use a clearly test-only fixture key. This demonstrates the authentication boundary without configuring or exposing any real credential.

HMAC protects against a storage writer who can alter stored records but does not possess the separately managed key. It does not protect against compromise of the application process or key, and it does not make pickle safe. The framework codec still requires trusted checkpoint provenance and strict storage access controls. No production secret, D1 write, Worker change, or migration is part of this prototype.

## Authenticated file-backed restart fixture (2026-11-02)

The isolated evaluation now includes `FileEnvelopeCheckpointStorage`, a local-filesystem adapter derived from the tested in-memory adapter. It loads and authenticates records on construction, uses SHA-256-derived filenames to avoid path traversal, rejects symlinks and oversized/non-private files, writes through a 0600 temporary file followed by atomic replace and directory fsync, and ignores orphan temporary files from interrupted writes. The directory must be private (0700) on POSIX systems.

The test suite creates a checkpoint, constructs a fresh adapter instance with the same test key, and verifies state recovery after the original instance is discarded. It also verifies delete persistence, wrong-key/corrupt-record rejection, private file modes, and tolerance of an orphan temporary file.

This is a local restart/recovery fixture only. It does not establish multi-process locking, distributed concurrency, backup/retention, key rotation, or production-grade filesystem hardening. It is not wired to the engine runtime or D1. Checkpoint values still use the pinned framework's pickle-bearing codec, so the directory must remain trusted and access-controlled.

## Decision and safety

Do not replace the current runtime or add these dependencies to default engine requirements yet. The experiment remains isolated and optional. The evaluation sends zero provider requests, uses zero paid services, requires no credentials, and does not change production data or deployment state.

## References

- https://github.com/microsoft/agent-framework/blob/main/LICENSE
- https://github.com/microsoft/agent-framework/blob/main/SECURITY.md
- https://github.com/microsoft/agent-framework/blob/main/python/packages/core/pyproject.toml
- https://github.com/microsoft/agent-framework/blob/main/python/packages/openai/pyproject.toml
- https://learn.microsoft.com/agent-framework/integrations/openai-endpoints
- https://github.com/microsoft/agent-framework/releases/tag/python-1.24.0
