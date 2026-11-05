# Shared-queue isolation design — not implemented for D1

## Consumer and producer inventory at main a72c48f

- Local `engine.worker.run_once` → `TaskStore.claim_next`; prototype now
  quarantines `research-pilot:` keys in generic claim, dependency blocking, and
  rejects them at generic local enqueue. `recover_stale` can still terminally fail
  expired one-attempt pilots. SQLite locking and tests do not prove remote D1.
- Remote `engine.worker.run_once` → `RemoteTaskStore.claim_next` → Worker
  `POST /v1/tasks/claim`. The D1 SQL excludes Codespace task types, **not** pilot
  identities; its recursive dependency blocking can mutate pilot rows too.
- Codespace bridge → `POST /v1/codespace/tasks/claim` selects Codespace types;
  release remains hard-disabled. Re-check every principal/route before rollout.
- Admission includes local/remote CLI and `TaskStore.enqueue`, Worker
  `/v1/tasks/enqueue`, GitHub comment ingest, workflows, and Worker workflow
  dispatch's batched task insert. Audit direct database writers before claiming
  this inventory is exhaustive. Operation-store claims are a different ledger.

## Proposed D1 contract (design, not proven behavior)

Reserve an immutable pilot namespace at **every** generic admission path and
exclude it from generic claim and dependency-blocking updates in both local and
D1 implementations. Do not rely solely on a prompt, in-process lock, public
queue idle snapshot, or Workflow concurrency. Validate privilege and task key
before a targeted API; guard collisions across generic and specialized writers.
Define canonical public payload bytes and identity fields: type, title, payload,
attempt limit, priority, dependency set and allowed lifecycle/mode. Same-key
same-identity returns existing ID plus `created=false` regardless of terminal
state; any mismatch is conflict. Never convert a replay to new execution.

A single database-serialized admission must bind key to identity and create at
most one row and audit record; targeted claim must predicate on exact ID, key,
immutable identity, pending status, zero attempts and authorized mode and use
an atomic conditional update/return with a fresh lease. An audit failure must
roll back the state change. Completion and all transitions require an unexpired
matching lease/fencing token. A stale or ambiguous provider dispatch is not
replayed; a stable local ID cannot itself guarantee exactly-once remote work.
Evaluate D1 `batch` semantics and failure/readback handling against the official
Cloudflare documentation before deciding the actual SQL and transaction design.
No schema change unless review proves a necessary invariant cannot be expressed
with current schema. Do not assert that a pre-read and separate D1 batch are atomic.

Minimum verification: race real local SQLite connections and actual generic
consumers, then local D1 integration for concurrent admissions/claims, unrelated
rows, blocked dependency propagation, malformed and conflicting identity,
stale lease and audit rollback. Each distinct principal and producer needs a
negative test. Local mocks or SQLite success are not remote D1 evidence; a
separate cost-verified, authorized isolated Staging run is needed later. Enforce
model evidence, credential scoping, zero-cost controls and no-fallback before
any enqueue/provider invocation; none is established by this design.
