# Autonomous Staging lifecycle — 2026-11-07

## Default operation

- Engine CI runs on pushes, pull requests and daily at 04:17 UTC.
- Offline policy, Python, Worker, local D1 and local Workflows tests run on every CI event.
- A successful push to `main` and the daily schedule automatically start the Staging lifecycle only after a read-only Cloudflare billing preflight establishes eligibility.
- Engine CI exposes no `workflow_dispatch` trigger. No person needs to open Actions or manually start the routine lifecycle.
- The D1 Task Runner has its own five-minute schedule. Its `workflow_dispatch` path is an internal GitHub App/watchdog fallback for missed scheduled runs, not an owner-operated control. All remote Worker calls and task batches are gated by the same billing preflight.

## Zero-cost preflight

The preflight verifies **both** account-level add-on subscriptions and every Cloudflare zone plan. Cloudflare's [Billing FAQ](https://developers.cloudflare.com/billing/understand/faq/) distinguishes per-domain plans (Free/Pro/Business/Enterprise) from account/domain add-on subscriptions (Workers, R2, Load Balancing, Cache Reserve, etc.). An empty account subscriptions array therefore means no add-ons were returned; it does **not** prove every domain is on Free.

### Live finding (2026-11-07): empty add-on inventory is not Free-plan evidence

After the SinglePage correction merged, main run **42328401696** successfully queried the account subscriptions endpoint and returned HTTP 200 with an empty `result` list. The previous guard correctly reported `empty_subscription_inventory_unverified` and skipped remote effects. This is evidence that the request works and no add-on records were returned; it is **not** evidence that every zone is Free. PR #683 adds the missing zone-plan checks below. Until the combined account-and-zone check verifies every zone, the cost gate remains closed.

1. **Account add-ons:** `GET /accounts/{account_id}/subscriptions`, with **no** query string. Cloudflare documents this as a single-page list; the [API reference](https://developers.cloudflare.com/api/resources/accounts/subresources/subscriptions/methods/get/) declares no `page`/`per_page` parameters and the generated [Python SDK](https://developers.cloudflare.com/api/python/resources/accounts/subresources/subscriptions/methods/get/) returns `SyncSinglePage[Subscription]`. The earlier implementation appended undocumented pagination parameters and received HTTP 400. The parser validates the returned list and optional metadata, finite nonnegative prices, subscription states and plan identity. An empty add-on inventory is acceptable only as the absence of add-ons, never as standalone proof of Free.
2. **Zone inventory:** `GET /zones?account.id={account_id}&page=1&per_page=20`. The [List Zones API](https://developers.cloudflare.com/api/resources/zones/methods/list/) documents these pagination/filter parameters. The safety gate supports at most 20 zones per run and fails closed if the inventory is larger or incomplete.
3. **Each zone's current plan:** `GET /zones/{zone_id}/subscription`. The [Zone Subscription Details API](https://developers.cloudflare.com/api/resources/zones/subresources/subscriptions/methods/get/) returns the current `rate_plan`, price and state. Every zone must have an explicit zero-price `free` or non-external `partners_free` plan, a stable Provisioned/Paid state, and no external/contract-managed plan. Any Pro/Business/Enterprise, nonzero price, trial, awaiting-payment, missing or unknown plan blocks remote operations.

The preflight requires Cloudflare **Billing Read** and **Zone Read across all zones in the account**. A token scoped to selected zones is insufficient: the API may return a complete list of only the zones visible to that token, hiding a paid zone. The owner must configure and verify an all-zones Zone Read scope plus Billing Read. A missing permission or uncertain scope is a closed gate, not a reason to skip the zone check. The read-only billing inventory workflow invokes the same parser so CI and manual inventory cannot drift. It publishes only a boolean, sanitized reason and bounded counts; response bodies, zone names/IDs, account identifiers and secrets are never logged.

Cloudflare documents Workers Free as the default plan and documents the Free/paid distinctions and limits in its current [Workers pricing](https://developers.cloudflare.com/workers/platform/pricing/) and [Workers limits](https://developers.cloudflare.com/workers/platform/limits/) references. The engine must not infer account-specific entitlements from public documentation alone.

## Automated Staging lifecycle

When the zero-cost preflight passes, the workflow:
1. Validates Staging and Production D1 identities are distinct and match the reviewed targets.
2. Applies pending migrations to Staging only.
3. Deploys the isolated Staging Worker on `workers.dev` only.
4. Generates ephemeral consumer/admin capabilities, verifies the Worker and researcher lane, and revokes temporary secrets on exit.
5. Verifies the monetary ledger and lane isolation.
6. Publishes sanitized operational telemetry to Staging D1 and verifies authenticated mobile status.

If zero-cost eligibility cannot be proven, all Staging side effects skip automatically while local tests and public research discovery continue.

## Separate gates

- Research-candidate persistence remains disabled until its dedicated Staging token is present, idempotency/readback controls are verified, and a separate Staging proof succeeds.
- Issue-comment task ingestion is separately guarded; it must preserve durable replay/reconciliation for comments skipped while billing is unverified.
- Groq and Gemini inference remain disabled; no paid fallback is permitted.
- The separate `raawaa-image-route-check` workflow is a bounded Workers AI free-route check, not part of Engine CI. It may generate one 1024×1024 image at four steps only after the zero-cost preflight passes. The route estimates 57.6 neurons per image against the Workers Free 10,000-neuron daily allowance. On Workers Free, exhausted daily allowance must fail the request; never upgrade, switch to a paid plan, or fall back to a paid provider. This is the only currently enabled bounded Workers AI inference path.

## Boundaries

- No Production D1 migrations or Production Worker deployment.
- Fixed Staging and Production identifiers are checked before any remote write.
- Temporary tokens are generated per run and revoked in cleanup steps.
- Live Groq/Gemini model inference is not part of Engine CI. The separate Workers AI image check is bounded and gated as described above.
- No owner-operated manual dispatch is needed for routine CI or Task Runner scheduling.

## Verification

`scripts/check_workflow_dispatch_guards.py`, `tests/test_workflow_dispatch_guards.py`, `tests/test_provider_workflow_security.py`, and `tests/test_cloudflare_zero_cost_boundary.py` enforce the automatic push/schedule triggers, main-ref restriction, account/zone billing evidence and mandatory zero-cost preflight dependencies.


## Control Worker deployment boundary — 2026-11-07

The authenticated Control Worker deployment workflow now requires both the existing explicit `[deploy-control-worker]` merge marker and a successful read-only Cloudflare add-on + all-zone Free-plan preflight. The preflight runs before D1 migration checks, bearer verification and Worker deployment. Unknown, incomplete, inaccessible, paid, trial or HTTP-error billing evidence fails closed. No deployment is performed by this guard change.
