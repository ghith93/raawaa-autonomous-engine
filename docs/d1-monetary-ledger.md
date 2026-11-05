# Durable D1 monetary ledger

## Purpose and safety boundary

The D1 monetary ledger is the shared source of truth for model spend across ephemeral and concurrent cloud runners. It stores integer nano-USD, not floating-point currency. It is independent of the token/request quota ledger.

This implementation does not enable model inference. Provider profiles remain disabled, policy remains `safe_dry_run`, live inference remains off, paid fallback remains off, and no provider request is sent by schema or local integration tests.

## Schema

Migration `0006_monetary_budget_ledger.sql` adds:

- `monetary_budget_policy`: the operator-owned hard caps in integer nano-USD (seeded by migration 0006 at $0.40/day, $12/month, $0.20/task as ledger guardrails). The API token cannot change these values; every new reservation must match the D1 policy exactly. These row values are guardrail/test fixtures only: the active repository policy is zero-spend (all caps USD 0, paid calls disabled), so they do not authorize any spend, and the isolated Staging harness exercises them with synthetic reservations that are never settled against a provider.
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

## Live-inference hard-stop gate

The repository policy keeps `model_policy.provider_hard_stop_verified=false` and `provider_hard_stop_evidence=null`. The live-runtime factory checks this flag before constructing any provider adapter, even when a provider profile is marked free and an API key is present. If the flag is ever changed to true, policy validation also requires auditable evidence containing the provider, account/scope, enforced hard limit, verification timestamp, and an HTTPS source. This is intentionally separate from Cloudflare's Free-tier infrastructure ceilings: those protect Workers/D1 usage but do not prove a model provider will block charges.

The paid fallback remains disabled. Do not set the provider hard-stop flag or the live-inference switch based only on a free-tier label, pricing page, quota error, or notification-only budget alert.

## Python integration

- `engine/d1_monetary_ledger.py` implements the existing `UsageLedger` interface against the authenticated Worker endpoint.
- `engine/live_runtime.py` selects both `D1MonetaryLedger` and `D1QuotaLedger` when `RAAWAA_ENGINE_API_URL` is configured and requires `RAAWAA_ENGINE_API_TOKEN`.
- In GitHub Actions, absence of the control Worker URL blocks cloud live runtime construction. It never silently falls back to ephemeral local SQLite.
- Local CLI development may continue to use the existing SQLite ledger.

## Provider-side hard-stop research

The currently documented free tiers do not provide a common, verified hard-spend control:

- **Groq:** organization-wide monthly spend limits are available only on paid-tier accounts with organization-owner permissions. Spend tracking updates about every 10–15 minutes, so a small overrun is possible before new calls are blocked. The Free tier does not expose this spend-limit feature.
- **Gemini API:** the Free tier has no billing-account spend cap. Prepay stops service when the balance reaches zero, but Google documents roughly 10 minutes of billing-pipeline latency and possible overages for long-running work. Project spend caps are experimental and also subject to latency.
- **OpenRouter:** its pricing matrix lists the broader “Budgets & Spend Controls” feature as unavailable on Free, but newer official OpenRouter guidance says per-key credit limits and Guardrails can be configured on Free and pay-as-you-go accounts; per-key limits reject requests after the configured credit cap, with daily, weekly, or monthly resets. Workspace-wide budgets are a separate, higher-tier feature. The documentation is not fully aligned, and Free-plan Management API access is listed as unavailable, so do not assume the account exposes every control through the API. A dedicated Engine key with a monthly credit cap and a restrictive model/provider allowlist is a promising provider-side hard-stop candidate, but it must be configured and verified in the actual account dashboard/API, including whether BYOK spend is included.

Therefore none of these controls is currently verified for the user's connected provider accounts. The runtime requires `provider_hard_stop_verified=true` plus auditable account/scope/limit evidence before constructing a provider adapter. Keep this false until the actual account tier and enforced limit are independently checked. A D1 reservation ledger is an important local safeguard, not proof that the provider itself cannot charge beyond the configured budget.

References:
- [Groq Spend Limits](https://console.groq.com/docs/spend-limits)
- [Gemini API Billing and Spend Caps](https://ai.google.dev/gemini-api/docs/billing)
- [OpenRouter Pricing and Free-plan controls](https://openrouter.ai/pricing)
- [OpenRouter API key limits and budgets](https://github.com/OpenRouterTeam/docs/blob/main/api_reference/limits.mdx)
- [OpenRouter team spend controls and per-key limits](https://openrouter.ai/blog/tutorials/team-spend-controls-setup/)
- [OpenRouter Guardrails, budget enforcement and model/provider allowlists](https://openrouter.ai/blog/announcements/guardrails/)
- [OpenRouter Guardrail policy changes, 30 October 2026](https://openrouter.ai/blog/announcements/guardrail-policies/)

## Cloudflare infrastructure billing boundary

Cloudflare documents account-level Spend Policies with a `usage_block` action for Workers and Durable Objects. This blocks further usage of the selected Cloudflare product until the billing period ends; it is not scoped to this Raawaa Worker. The current account inventory workflow receives HTTP 404 from the Spend Policies endpoint, so no account-level Cloudflare hard stop has been verified. We do not create or enable a product-wide usage block because it could disable unrelated and production Workers.

Cloudflare's Billable Usage dashboard and budget alerts are documented as Pay-as-you-go features; budget alerts are informational notifications, not a usage cap. Keep paid overages disabled and the infrastructure spend allowance at zero. The D1 monetary policy in this document controls model-provider spend, not Cloudflare infrastructure billing.

References:
- [Cloudflare Spend Policies API](https://developers.cloudflare.com/api/resources/billing/subresources/spend_policies/)
- [Cloudflare Budget Alerts](https://developers.cloudflare.com/billing/manage/budget-alerts/)
- [Cloudflare Billable Usage](https://developers.cloudflare.com/billing/manage/billable-usage/)

## Verification and remaining gates

The local D1/Workerd integration suite exercises concurrent global daily/monthly and per-task reservations, rejection of caller-raised caps, pre-I/O-only release, post-start release rejection, ambiguous state across restart, idempotent settlement, over-estimate quarantine, route rejection, legacy spend backfill, and authenticated mobile status. Monthly-cap concurrency is tested locally by temporarily changing only the ephemeral local D1 policy. The remote Staging harness tests concurrent daily, monthly, and per-task monetary reservations; the monthly case temporarily adjusts only the isolated Staging policy row and restores the repository-approved caps in a `finally` block. It also tests policy tamper rejection and concurrent D1 token quota reservations. Python adapter tests cover nano-USD conversion, authentication, status reads, and fail-closed transport behavior.

The issue is not complete until:
1. This branch is reviewed and merged.
2. Migration 0006 is applied to the isolated Staging D1 database by the existing green-main CI migration gate.
3. The CI-gated `staging-worker-verify` job deploys an isolated Staging Worker after successful tests and Staging migrations, installs a per-run ephemeral `ENGINE_API_TOKEN` that is masked and not stored in GitHub, and runs remote verification. No production Worker deployment is implied.
4. Remote Staging tests verify concurrent-client reservations against shared D1, Worker redeploy persistence, duplicate settlement, ambiguous provider outcomes, and over-cap behavior. They do not yet run two independent GitHub Actions runners concurrently.
5. The dedicated `monetary_ledger` section of authenticated mobile status reports aggregate D1 totals, policy caps, pending/ambiguous counts, and quarantined-route count; it omits reservation IDs, task IDs, and model names. The separate legacy `model_costs_usd` panel remains grouped by provider with aggregate request/token/cost totals. The authenticated operator ledger endpoint returns bounded unresolved reservation metadata for internal reconciliation. No credentials are returned.
6. Provider-side hard-stop semantics are independently verified. The Cloudflare spend-policy endpoint previously returned HTTP 404, so account-wide infrastructure spend blocking remains unverified.

Do not enable live inference, paid fallback, or production deployment as part of this implementation.
