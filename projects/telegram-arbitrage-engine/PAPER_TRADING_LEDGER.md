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

## Forward-only outcome observation

New flash-swap captures use observer schema 1 and persist the route fees, borrowed token/amount, optional gas scenario, and original block-pinned token-decimal metadata. The candidate identity is versioned so previously captured append-only events are not rewritten. Legacy candidates without this metadata are intentionally not auto-interpreted; recapture them with the current simulator.

Run a later observation with:

```bash
arb-engine paper-observe --candidate-id <candidate-id>
```

The observer:
- verifies the candidate's existing event hash chain and original route/snapshot block agreement;
- fetches all route pools (and the optional gas-valuation pool) at one later canonical block using read-only RPC calls;
- rejects the same/earlier block, a non-later block timestamp, a reorged block, mismatched chain/pools, or changed token decimals;
- replays the exact recorded borrow amount, fees, and optional gas scenario against the later reserves;
- appends one `outcome_observed` event per observed block hash, making retries idempotent while allowing later observations to form a time series.

This is a forward-only reserve-math observation, not a historical backtest, executable quote, order simulation, realized PnL, or execution guarantee. It does not place, sign, or submit an order. The RPC reader is read-only. The default ledger is local SQLite; D1 is used only when `RAAWAA_PAPER_LEDGER_BACKEND=d1` is explicitly set and its authenticated Worker endpoint is configured. There is no fallback between backends.

## Forward-only outcome observation

New flash-swap captures use observer schema 1 and persist the route fees, borrowed token/amount, optional gas scenario, and original block-pinned token-decimal metadata. The candidate identity is versioned so previously captured append-only events are not rewritten. Legacy candidates without this metadata are intentionally not auto-interpreted; recapture them with the current simulator.

Run a later observation with:

```bash
arb-engine paper-observe --candidate-id <candidate-id>
```

The observer:
- verifies the candidate's existing event hash chain and original route/snapshot block agreement;
- fetches all route pools (and the optional gas-valuation pool) at one later canonical block using read-only RPC calls;
- rejects the same/earlier block, a non-later block timestamp, a reorged block, mismatched chain/pools, or changed token decimals;
- replays the exact recorded borrow amount, fees, and optional gas scenario against the later reserves;
- appends one `outcome_observed` event per observed block hash, making retries idempotent while allowing later observations to form a time series.

This is a forward-only reserve-math observation, not a historical backtest, executable quote, order simulation, realized PnL, or execution guarantee. It does not place, sign, or submit an order. The RPC reader is read-only. The default ledger is local SQLite; D1 is used only when `RAAWAA_PAPER_LEDGER_BACKEND=d1` is explicitly set and its authenticated Worker endpoint is configured. There is no fallback between backends.

## Remote D1 persistence adapter (staged)

The repository also contains an authenticated Worker endpoint at `POST /v1/paper-ledger` and a Python HTTP client, backed by additive migration `0004_paper_event_ledger.sql`. The flash-swap CLI has an explicit opt-in D1 backend, but it is **not** deployed or migrated against any production D1 database. It requires a separately deployed Worker and an explicitly migrated non-production database before use.

- D1 is intended to be the cross-run source of truth for scheduled ephemeral workers; local SQLite remains the deterministic offline/test adapter.
- The Worker accepts only the fixed event vocabulary, validates timezone-aware timestamps, block provenance, uint256 raw integer strings, and cross-runtime canonical JSON payloads; it caps request/payload sizes and requires the existing bearer authentication.
- Appends use a candidate-head compare-and-swap followed by conditional event/checkpoint inserts in one D1 batch. A stale concurrent writer cannot advance the head or create a fork; the caller receives a conflict and may retry with bounded backoff.
- Replaying an identical event ID and content returns the original sequence/hash; conflicting event-ID reuse is rejected.
- Event and checkpoint UPDATE/DELETE operations are rejected by D1 triggers. The candidate head is mutable only through the CAS path.
- The Python D1 client fails closed on HTTP, transport, or malformed-response errors; it never silently falls back to a local SQLite file.

The flash-swap CLI can now select this adapter only when both `--record-paper` and `RAAWAA_PAPER_LEDGER_BACKEND=d1` are explicitly set. It reads `RAAWAA_ENGINE_API_URL` and `RAAWAA_ENGINE_API_TOKEN` from the environment (and optional paired Cloudflare Access service-token variables). It never accepts or prints secrets as command-line arguments. D1 initialization, HTTP, and verification errors fail closed; there is no SQLite fallback. The default backend remains local SQLite, and no ledger is written without `--record-paper`.

Example environment setup (supply secrets through the runtime's secret store, not shell history or committed files):

```bash
export RAAWAA_PAPER_LEDGER_BACKEND=d1
export RAAWAA_ENGINE_API_URL=https://<deployed-worker-host>
# Set RAAWAA_ENGINE_API_TOKEN through a protected secret environment.
arb-engine flash-swap-sim ... --record-paper
```

Candidate IDs and event timestamps are derived deterministically from the pinned block, pools, borrowed asset/amount, fees, and gas scenario so a retry of the same simulation reuses the same event IDs and block-time timestamp. A changed scenario creates a distinct candidate.

The current unit suite exercises the Worker compare-and-swap path with a deterministic concurrency mock and validates the migration against SQLite. This is not proof of Cloudflare D1 transaction semantics. Before activation, apply the migration only to a dedicated non-production D1 database and test concurrent appends, identical replay, conflicting replay, event/checkpoint integrity, restart, and backup/restore. Production migration/deployment remains a separate reviewed release step. No live trading or external transaction capability is provided.
