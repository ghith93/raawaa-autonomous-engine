# Side-effect operation contract

## Status

The operation ledger is implemented as a persistence primitive. It is not connected
to the worker, external providers, messaging, commerce, billing, or deployment.
All current worker behavior remains dry-run.

## Required handler protocol

1. Derive a stable idempotency key from the business intent, not a retry number.
2. Call `OperationStore.prepare(type, key, payload)` before any external request.
   Reusing a key with different input is rejected.
3. Claim the operation. Only a `pending` record can be claimed; its claim token
   fences stale local workers from finalizing it.
4. Send the same idempotency key downstream when supported, then persist the
   external reference and confirmed result.
5. On an ambiguous response, stop the attempt and mark it `unknown`. Never replay it.
6. Reconcile only after independently checking the downstream system. A confirmed
   success becomes terminal. Return to `pending` only when non-execution is confirmed.

## Guarantees and limitations

- A unique database constraint prevents duplicate operation intents for the same key.
- A transaction allows only one local claim from `pending`.
- Claim tokens prevent stale local workers from finalizing an operation.
- `executing` and `unknown` operations are never automatically replayed.
- Reconciliation cannot release a still-`executing` claim. An attempt must first end
  and be marked `unknown`, preventing an operator from accidentally releasing a
  live claim while its request may still be in flight.
- A crash can leave an operation `executing`; an operator/recovery procedure must
  establish that the old attempt is no longer active before marking it unknown.
- This is not exactly-once delivery. The downstream API must honor the same key or
  expose reliable lookup/reconciliation to prevent duplicate effects across failures.
- The ledger does not authorize external actions; existing policy and approval gates
  remain mandatory.

## Validation

Run `python -m unittest discover -s tests -v`.
