# Shared-queue isolation design — D1 pilot admission not implemented

## Consumer and producer inventory at main a72c48f

- Local `engine.worker.run_once` → `TaskStore.claim_next`; prototype now
  quarantines `research-pilot:` keys in generic claim, dependency blocking, and
  rejects them at generic local enqueue. This draft also excludes them from
  generic `recover_stale`; expired pilots remain quarantined, not requeued.
  SQLite locking and tests do not prove remote D1.
- Remote `engine.worker.run_once` → `RemoteTaskStore.claim_next` → Worker
  `POST /v1/tasks/claim`. Main a72c48f does not exclude pilot identities. This
  draft adds exclusion to claim and recursive dependency blocking, and rejects
  generic enqueue and generic stale recovery (also called by Worker cron); it is
  tested only against local Wrangler/D1, not deployed or proved against remote
  D1. Other mutation paths remain to be inventoried.
- Codespace bridge → `POST /v1/codespace/tasks/claim` selects Codespace types;
  release remains hard-disabled; the shared claim SQL includes the exclusion,
  but its active Codespace branch has not been exercised. Re-check every
  principal/route before rollout.
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

## D1 documentation evidence and transaction sketch

On 2026-11-05, Cloudflare's [D1 batch documentation](https://developers.cloudflare.com/d1/worker-api/d1-database/#batch)
states that a batch runs as a SQL transaction and a statement failure aborts or
rolls back the sequence. [D1 limits](https://developers.cloudflare.com/d1/platform/limits/)
describe each individual database as single-threaded, queuing concurrent requests
and returning overload errors when the queue is full. These are provider claims,
not proof that a particular pilot implementation is safe. SQL errors, overload,
timeouts, lost responses and retry behavior need independent tests.

Candidate admission batch: parameterized `INSERT ... ON CONFLICT DO NOTHING`
for an immutable unique idempotency key; then an audit insert **conditioned on
the inserted task ID**, followed by a lookup within the same batch. Evaluate the
returned row against the entire identity and lifecycle; a conflict must never
produce a second task or audit. The returned created flag must come from actual
insert changes, not from a pre-read. On response loss, reconcile by immutable
key and exact identity; if the outcome cannot be verified, return unknown and
do not dispatch. Do not use `OR IGNORE` to hide non-uniqueness failures.

Candidate claim batch: conditional `UPDATE ... RETURNING` for exact task ID,
reserved key, immutable identity, pending state, attempt count zero and correct
mode, assigning a fresh token and expiry; then an audit insert conditioned on
that exact newly installed token and running state. Both statements must share
one D1 batch; no provider call until its successful result is parsed and lease
ownership is revalidated. Database triggers, schema constraints, collisions,
ambiguous batch results and session/readback consistency must be tested rather
than assumed. This sketch is intentionally not code or an authorization to write.

## Cloudflare Workers AI backup decision (2026-11-05)

The owner requests Workers AI Free as a backup. Treat backup as an explicitly
selected new task/route, not silent provider rotation or retry following an
ambiguous dispatch. No binding, gateway route, model call or deployment has
been enabled. Use the existing quota gateway and durable invocation/monetary
ledger, not a parallel dispatcher; the existing gateway's routing behavior
must be reviewed for strict one-route/no-fallback semantics before reuse.

Cloudflare documents 10,000 neurons/day at no charge on Workers Free, while
Workers Paid incurs overage, and some models require paid billing. See [pricing]
(https://developers.cloudflare.com/workers-ai/platform/pricing/) and [errors]
(https://developers.cloudflare.com/workers-ai/platform/errors/). A quota 3036,
capacity 3040, or paid-model 5035 must not initiate an automatic fallback.
Catalog visibility does not prove account plan, eligible model, billing route,
or zero infrastructure overage. AI Gateway unified billing/prepaid credits are
not authorized. Public fixtures only; verify [data usage]
(https://developers.cloudflare.com/workers-ai/platform/data-usage/) before
transmitting anything. Account-specific Workers Free evidence, usage and
billing reconciliation, operator-owned privacy decision where needed, D1
isolation, current evidence and containment remain gates. Unknown = disabled.
No claim that a free allowance alone proves the account-wide $15 hard ceiling.

## Mutation ownership and direct-writer inventory

Generic local heartbeat, finish and retry now reject reserved keys, including
conditional SQL predicates. No targeted local completion, heartbeat or recovery
is implemented: local pilot rows cannot complete through a supported API. Terminal
replay tests seed synthetic terminal state, not a working completion lifecycle.
Worker generic heartbeat/finish/retry and invocation paths are guarded in this
draft, with local Wrangler/D1 tests; Codespace execution remains release-gated.
These do not prove remote isolation or privilege separation.

Repository search on 2026-11-05 identified the following task writers and readers:

- `TaskStore.enqueue`, `RemoteTaskStore.enqueue`, CLI enqueue and GitHub comment
  ingestion through `/v1/tasks/enqueue`; the draft rejects reserved keys in the
  two generic store implementations. `engine.worker` recovers then claims via
  those stores. `codespace_bridge` uses its separately gated Worker route.
- Worker workflow dispatch directly inserts tasks but derives keys with the
  literal `workflow-step:` prefix (`worker/workflow-dispatch.js`); collision
  with `research-pilot:` is impossible under that implementation. Other
  dependency references to pilot rows still need design and testing.
- `scripts/verify_staging_monetary_ledger.py` uses privileged direct D1 SQL for
  isolated generated staging fixture insertion/deletion. Its authority can
  bypass application namespace protections; it is not a security boundary.
  All external/manual SQL holders likewise remain trusted and not enumerable
  from a source-code scan. `run_groq_free_inference_smoke.py` uses remote enqueue.
- Local `TaskStore.list_tasks`/`audit_events` and authenticated Worker
  `GET /v1/tasks` expose pilot records to readers with generic access. The
  payload policy permits public research only but is heuristic and does not
  prove confidentiality. Do not use this shared queue for private payloads or
  infer per-principal read isolation. CLI and remote list paths inherit this.

This source search is not proof about outside writers, triggers, deployed versions,
or all administrative credentials. Specialized stale reconciliation remains
unimplemented; expired pilots stay quarantined and are not retried.

### Owner plan confirmation

On 2026-11-05 the owner reported the Workers dashboard plan as **Free**. This
is operator attestation, not a direct machine readback; account identity, exact
AI billing route, remaining allowance, and other charges remain unverified.
Cloudflare [lists Gemma 4 26B as Free-plan eligible](https://developers.cloudflare.com/changelog/post/2026-07-28-models-require-workers-paid/),
and the account catalog GET showed its slug, not inference entitlement. Use
standard Workers AI billing only, never AI Gateway unified billing or prepaid
credits; neither an AI binding nor an actual route is yet implemented/enabled.
Pre-dispatch gates and D1 isolation remain unresolved, regardless of plan.
