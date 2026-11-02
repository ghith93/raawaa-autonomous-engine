# Append-only paper-trading research ledger

## Purpose

`PaperTradingLedger` stores a provenance-linked event history for candidate discovery and simulation. It is a persistence primitive only: it does not discover routes, construct orders, sign transactions, send transactions, or claim realized PnL.

The existing `OpportunityLedger` remains unchanged and continues to store indicative DEX Screener observations. Do not merge indicative USD marks into the paper-trading event stream as if they were executable quotes.

## Event lifecycle

Allowed event types:
- `candidate_detected`
- `snapshot_captured`
- `route_simulated`
- `gas_scenario_evaluated`
- `paper_order_simulated`
- `outcome_observed`
- `rejected`
- `expired`

The caller supplies a stable `candidate_id`, unique `event_id`, timezone-aware event time, and JSON payload. Snapshot-derived events require `chain_id`, `block_number`, and `block_hash` together. Route simulation events additionally require a strategy ID, two distinct pool addresses, and input/output token addresses. Gas scenario events require explicit gas units, gas price, gas cost, and net raw-unit fields.

All raw token and gas values are stored as decimal strings, not SQLite INTEGERs, so uint256-sized values are not truncated to signed 64-bit integers. Payloads use a cross-runtime canonical JSON subset: null, strings, booleans, safe integers, arrays, and string-keyed objects. Floating-point values and integers outside JavaScript's safe range are rejected; store prices and large raw-unit values as decimal strings.

## Integrity and replay

- Events receive a monotonically increasing sequence number per candidate.
- Each event hash covers the canonical event record and the previous event hash.
- A separate append-only checkpoint table anchors each sequence/event hash, allowing verification to detect a missing tail event as well as a changed row.
- SQLite triggers reject event/checkpoint UPDATE and DELETE operations.
- Replaying the same event ID with identical content is an idempotent no-op. Reusing an event ID with different content raises an error.
- `verify_chain(candidate_id)` checks sequence continuity, event hashes, previous hashes, and checkpoint agreement.

This is tamper-evident local storage, not a cryptographic signature or a remote immutable audit service. An actor with full database-file control can remove triggers and rewrite both tables; production audit requirements would need an externally anchored checkpoint or remote write-once storage.

## Minimal example

```python
from datetime import datetime, timezone
from arb_engine.paper_ledger import PaperTradingLedger

ledger = PaperTradingLedger("data/paper-trading.sqlite3")
ledger.append_event(
    candidate_id="candidate-2026-11-02-001",
    event_id="candidate-detected-001",
    event_type="candidate_detected",
    occurred_at=datetime.now(timezone.utc),
    payload={"source": "read_only_scanner", "classification": "research_only"},
)
assert ledger.verify_chain("candidate-2026-11-02-001")
```

This example records only a discovery event. It does not create or submit a paper/live order. Future integrations must append route, snapshot, gas, and outcome evidence without mutating earlier events.

## Release gates before paper-performance reporting

1. Connect only canonical block-pinned pool snapshots and record source/age/hash provenance.
2. Record every candidate and rejection, not just candidates with positive model output.
3. Record explicit gas-price and gas-used scenarios separately from gross reserve surplus.
4. Add a paper outcome observer that evaluates the next canonical block(s) without look-ahead bias.
5. Measure missed inclusion, stale snapshots, reverts, and hypothetical slippage; do not assume all simulated candidates execute.
6. Validate event replay and hash-chain verification across process restart and backup/restore.
7. Keep all signing and transaction submission absent until a separate operator-approved security and execution gate.

## Remote D1 persistence adapter (explicit opt-in)

The authenticated Worker endpoint at `POST /v1/paper-ledger` now has a narrow Python client and is wired into `flash-swap-sim` behind an explicit backend selector. Local SQLite remains the default. D1 is selected only when `RAAWAA_PAPER_LEDGER_BACKEND=d1`; it then requires `RAAWAA_PAPER_LEDGER_URL` (HTTPS) and `RAAWAA_PAPER_LEDGER_TOKEN` from environment variables. The client never accepts credentials on CLI arguments, prints them, or falls back to SQLite after a D1 error.

Example environment configuration (set these in the process environment, not in source control):

```bash
export RAAWAA_PAPER_LEDGER_BACKEND=d1
export RAAWAA_PAPER_LEDGER_URL=https://YOUR-WORKER.example.workers.dev
export RAAWAA_PAPER_LEDGER_TOKEN=...
arb-engine flash-swap-sim ... --record-paper
```

- Without `--record-paper`, neither backend is written.
- Without the backend variable, local SQLite remains the default and existing offline tests are unchanged.
- D1 mode requires a separately deployed Worker and applied migration `0004_paper_event_ledger.sql` on a non-production database before use. This change does not deploy a Worker or apply any migration.
- The D1 client uses only the fixed `/v1/paper-ledger` append/list API; it does not expose arbitrary SQL or other Worker routes.
- Candidate IDs are derived from chain, pinned block hash, pool identities, borrowed token, and amount. Event times are derived from the pinned block timestamp so identical retries reproduce the same event content and IDs.
- D1 appends use the Worker's compare-and-swap candidate head, conditional event/checkpoint writes, and hash-chain verification. HTTP, transport, malformed-response, event conflict, and integrity failures stop the operation; no SQLite fallback occurs.
- The local SQLite adapter retains its existing storage and deterministic tests.

Before any production use, validate the adapter against a dedicated non-production D1 database: apply migration 0004 there, test concurrent appends, identical and conflicting replay, chain verification, process restart, and backup/restore. Production migration/deployment remains a separate approval-gated action. No wallet, signer, order, or transaction capability is introduced.
