# Durable D1 monetary ledger

## Purpose and safety boundary

The D1 monetary ledger is the shared source of truth for model spend across ephemeral and concurrent cloud runners. It stores integer nano-USD, not floating-point currency. It is independent of the token/request quota ledger.

This implementation does not enable model inference. Provider profiles remain disabled, policy remains `safe_dry_run`, live inference remains off, paid fallback remains off, and no provider request is sent by schema or local integration tests.

## Schema

Migration `0006_monetary_budget_ledger.sql` adds:

- `monetary_reservations`: one record per request reservation, with idempotency key, task/provider/model attribution, estimate, configured caps, lifecycle status, actual cost and token usage.
- `monetary_route_quarantines`: persistent provider/model quarantine after actual cost exceeds the estimate or the applicable hard cap.

All amounts are integer nano-USD. Reservations in `reserved`, `in_flight`, or `ambiguous` are counted against every subsequent daily and monthly budget window until settled or released while still pre-I/O. Settled spend is counted in the UTC window containing `settled_at`; task totals are cumulative across that task's history.

## Lifecycle and API

Authenticated endpoint: `/v1/monetary-ledger`.

| Action | Allowed transition | Safety behavior |
|---|---|---|
| `reserve` | create `reserved` | One conditional D1 insert enforces global daily/monthly caps and per-task cap across providers. Idempotency key replay returns the original reservation. |
| `start` | `reserved → in_flight` | Called immediately before provider I/O. |
| `ambiguous` | `in_flight → ambiguous` | Retains the full estimate after timeout, transport failure, malformed response, or uncertain provider outcome. |
| `release` | `reserved → released` | Rejected after provider I/O has been marked started. |
| `settle` | `in_flight|ambiguous → settled` | Records actual cost/tokens exactly once. Identical replay is accepted; conflicting replay is rejected. |
| `GET` | read-only status | Returns global spend, unresolved reservation metadata, and quarantined routes. |

An explicit provider HTTP 429 occurs after I/O begins, so the gateway settles it at zero cost rather than releasing the reservation. Token/request quota remains consumed. Other provider errors preserve an ambiguous monetary reservation and stop route rotation.

When actual cost exceeds its estimate, or the settled total exceeds the daily/monthly/task cap, the actual amount is retained and the provider/model route is quarantined. Subsequent reservations for that route are rejected.

## Python integration

- `engine/d1_monetary_ledger.py` implements the existing `UsageLedger` interface against the authenticated Worker endpoint.
- `engine/live_runtime.py` selects D1 when `RAAWAA_ENGINE_API_URL` is configured and requires `RAAWAA_ENGINE_API_TOKEN`.
- In GitHub Actions, absence of the control Worker URL blocks cloud live runtime construction. It never silently falls back to ephemeral local SQLite.
- Local CLI development may continue to use the existing SQLite ledger.

## Verification and remaining gates

The local D1/Workerd integration suite exercises concurrent per-task reservations, pre-I/O-only release, post-start release rejection, ambiguous state, idempotent settlement, over-estimate quarantine, route rejection, and authenticated mobile status. Python adapter tests cover nano-USD conversion, authentication, status reads, and fail-closed transport behavior.

The issue is not complete until:
1. This branch is reviewed and merged.
2. Migration 0006 is applied to the isolated Staging D1 database by the existing green-main CI migration gate.
3. A Staging Worker is deployed with its own operator-managed `ENGINE_API_TOKEN`; no production Worker deployment is implied.
4. Remote Staging tests verify cross-runner concurrency, crash/restart persistence, duplicate settlement, ambiguous provider outcomes, and over-cap behavior.
5. The authenticated mobile status surface reports the D1 monetary totals.
6. Provider-side hard-stop semantics are independently verified. The Cloudflare spend-policy endpoint previously returned HTTP 404, so account-wide infrastructure spend blocking remains unverified.

Do not enable live inference, paid fallback, or production deployment as part of this implementation.
