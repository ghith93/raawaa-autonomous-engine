# Durable D1 monetary ledger

## Purpose and safety boundary

The D1 monetary ledger is the shared source of truth for model spend across ephemeral and concurrent cloud runners. It stores integer nano-USD, not floating-point currency. It is independent of the token/request quota ledger.

This implementation does not enable model inference. Provider profiles remain disabled, policy remains `safe_dry_run`, live inference remains off, paid fallback remains off, and no provider request is sent by schema or local integration tests.

## Schema

Migration `0006_monetary_budget_ledger.sql` adds:

- `monetary_budget_policy`: the operator-owned hard caps in integer nano-USD (currently $0.40/day, $12/month, $0.20/task). The API token cannot change these values; every new reservation must match the D1 policy exactly.
- `monetary_reservations`: one record per request reservation, with idempotency key, task/provider/model attribution, estimate, policy caps, lifecycle status, actual cost and token usage.
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
- `engine/live_runtime.py` selects both `D1MonetaryLedger` and `D1QuotaLedger` when `RAAWAA_ENGINE_API_URL` is configured and requires `RAAWAA_ENGINE_API_TOKEN`.
- In GitHub Actions, absence of the control Worker URL blocks cloud live runtime construction. It never silently falls back to ephemeral local SQLite.
- Local CLI development may continue to use the existing SQLite ledger.

## Cloudflare infrastructure billing boundary

Cloudflare documents account-level Spend Policies with a `usage_block` action for Workers and Durable Objects. This blocks further usage of the selected Cloudflare product until the billing period ends; it is not scoped to this Raawaa Worker. The current account inventory workflow receives HTTP 404 from the Spend Policies endpoint, so no account-level Cloudflare hard stop has been verified. We do not create or enable a product-wide usage block because it could disable unrelated and production Workers.

Cloudflare's Billable Usage dashboard and budget alerts are documented as Pay-as-you-go features; budget alerts are informational notifications, not a usage cap. Keep paid overages disabled and the infrastructure spend allowance at zero. The D1 monetary policy in this document controls model-provider spend, not Cloudflare infrastructure billing.

References:
- [Cloudflare Spend Policies API](https://developers.cloudflare.com/api/resources/billing/subresources/spend_policies/)
- [Cloudflare Budget Alerts](https://developers.cloudflare.com/billing/manage/budget-alerts/)
- [Cloudflare Billable Usage](https://developers.cloudflare.com/billing/manage/billable-usage/)

## Verification and remaining gates

The local D1/Workerd integration suite exercises concurrent global daily/monthly and per-task reservations, rejection of caller-raised caps, pre-I/O-only release, post-start release rejection, ambiguous state across restart, idempotent settlement, over-estimate quarantine, route rejection, legacy spend backfill, and authenticated mobile status. Monthly-cap concurrency is tested locally by temporarily changing only the ephemeral local D1 policy. The remote Staging harness tests concurrent daily/per-task monetary reservations against the operator-owned caps, policy tamper rejection, and concurrent D1 token quota reservations. Python adapter tests cover nano-USD conversion, authentication, status reads, and fail-closed transport behavior.

The issue is not complete until:
1. This branch is reviewed and merged.
2. Migration 0006 is applied to the isolated Staging D1 database by the existing green-main CI migration gate.
3. The CI-gated `staging-worker-verify` job deploys an isolated Staging Worker after successful tests and Staging migrations, installs a per-run ephemeral `ENGINE_API_TOKEN` that is masked and not stored in GitHub, and runs remote verification. No production Worker deployment is implied.
4. Remote Staging tests verify cross-runner concurrency, crash/restart persistence, duplicate settlement, ambiguous provider outcomes, and over-cap behavior.
5. The authenticated mobile status surface reports the D1 monetary totals.
6. Provider-side hard-stop semantics are independently verified. The Cloudflare spend-policy endpoint previously returned HTTP 404, so account-wide infrastructure spend blocking remains unverified.

Do not enable live inference, paid fallback, or production deployment as part of this implementation.
