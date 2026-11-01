# Side-effect operation contract

## Status

The operation ledger is implemented as a persistence and coordination primitive.
It is not connected to the worker, external providers, messaging, commerce, billing,
or deployment. All current worker behavior remains dry-run.

## Required handler protocol

1. Derive a stable idempotency key from the business intent (not from a retry number).
2. Call `OperationStore.prepare(type, key, payload)` before any external request.
   Reusing a key with different input is rejected.
3. Claim the operation. Only a `pending` record can be claimed; the returned claim
   token fences stale local workers from finalizing it.
4. Send the same idempotency key to the downstream service when that service supports
   idempotency. Persist its external reference and confirmed result.
5. If the response is ambiguous (timeout, disconnect, process crash, or uncertain
   provider response), mark or leave the operation unresolved. Do not replay it.
6. Reconcile against the downstream system. Use `reconcile(..., "succeeded")` if the
   effect is confirmed, or `reconcile(..., "not_performed")` only when the downstream
   system confirms it did not happen. The latter returns the operation to `pending`.

## Guarantees and limitations

- A unique database constraint prevents duplicate operation intents for the same key.
- A transaction allows only one local claim from `pending`.
- Claim tokens prevent stale local workers from finalizing an operation.
- `executing` and `unknown` operations are never automatically replayed.
- This is not an exactly-once delivery guarantee. A downstream API must honor the
  same idempotency key, or expose a reliable lookup/reconciliation mechanism, to
  prevent duplicate effects across network/process failures.
- A crash after the external effect but before local success persistence leaves an
  `executing` record. It must be reconciled; do not reset it automatically.
- The ledger does not authorize an external action. Existing policy/approval gates
  remain mandatory.

## Validation

Run `python -m unittest discover -s tests -v`.
