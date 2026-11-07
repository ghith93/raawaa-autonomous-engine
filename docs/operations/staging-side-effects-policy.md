# Autonomous Staging lifecycle — 2026-11-07

## Default operation

- Engine CI runs on pushes, pull requests and daily at 04:17 UTC.
- Offline policy, Python, Worker, local D1 and local Workflows tests run on every CI event.
- A successful push to `main` and the daily schedule can start the Staging lifecycle only after a read-only Cloudflare billing preflight establishes eligibility.
- Engine CI exposes no `workflow_dispatch` trigger.
- The D1 Task Runner declares a five-minute schedule. The cron was re-registered at minutes `3,8,...,58`, but no genuine `event=schedule` run has yet been observed in the latest 100 main-branch Actions runs. Manual `workflow_dispatch` runs do not prove unattended execution. Require two successful scheduled runs on `main`, including the local fake-executor lifecycle, before declaring the Task Runner unattended. The existence of a `workflow_dispatch` trigger alone does not prove that a Cloudflare watchdog is dispatching it.

## Zero-cost preflight

The preflight verifies **both** account-level add-on subscriptions and every Cloudflare zone plan. Cloudflare's [Billing FAQ](https://developers.cloudflare.com/billing/understand/faq/) explicitly distinguishes per-domain plans (Free/Pro/Business/Enterprise) from account/domain add-on subscriptions (Workers, R2, Load Balancing, Cache Reserve, etc.). An empty account subscriptions array therefore means no add-ons were returned; it does **not** prove every domain is on Free.

1. Account add-ons: `GET /accounts/{account_id}/subscriptions`, with **no** query string. Cloudflare documents this as a single-page list; the [API reference](https://developers.cloudflare.com/api/resources/accounts/subresources/subscriptions/methods/get/) declares no `page`/`per_page` parameters and the generated [Python SDK](https://developers.cloudflare.com/api/python/resources/accounts/subresources/subscriptions/methods/get/) returns `SyncSinglePage[Subscription]`. The earlier implementation appended undocumented pagination parameters and received HTTP 400. The current parser validates the returned list and optional metadata, finite nonnegative prices, subscription states and plan identity. Empty add-on inventory is allowed only after zone plans are independently verified.
2. Zone inventory: `GET /zones?account.id={account_id}&page=1&per_page=50&type=full,partial,secondary,internal`. Cloudflare excludes `internal` zones by default, so the request explicitly includes all four zone types. The [List Zones API](https://developers.cloudflare.com/api/resources/zones/methods/list/) explicitly documents these pagination/filter parameters. The current safety gate verifies up to 20 zones from one 50-record page; a full page or a count above 20 fails closed. Cloudflare documents `total_count` as the total available without search parameters, so the guard validates but does not compare that unfiltered value against the account-filtered result.
3. Each zone: `GET /zones/{zone_id}/subscription`. The [Zone Subscription Details API](https://developers.cloudflare.com/api/resources/zones/subresources/subscriptions/methods/get/) returns the current `rate_plan`, price and state. Every zone must have an explicit Free plan, zero price, a stable Provisioned/Paid state, and no external/contract-managed plan. Any Pro/Business/Enterprise, nonzero price, trial, awaiting-payment, missing or unknown plan blocks remote operations.

The preflight requires Cloudflare **Billing Read** and **Zone Read** permissions, with Zone Read scoped to **all zones in the account**, not selected zones. Cloudflare does not expose a reliable API field proving a token's resource scope. Therefore every parser invocation also requires the repository Actions variable `CLOUDFLARE_ZONE_READ_SCOPE_ATTESTATION=all_account_zones`. The owner must inspect the token policy in Cloudflare and set this non-secret variable only after confirming the token is scoped to all account zones. Missing or different values fail closed before any Cloudflare API request. This variable is an owner attestation, not cryptographic verification of the token policy. It publishes only a boolean, sanitized reason and bounded counts; response bodies, zone names/IDs, account identifiers and secrets are never logged. The read-only billing inventory workflow invokes the same parser so CI and manual inventory cannot drift.

This remains a conservative account/zone billing gate, not a global no-charge guarantee for every external provider or model route. Groq/Gemini and Workers AI inference entitlements remain separate gates. If any required evidence is unknown, remote operations stay skipped.

Cloudflare documents Workers Free as the default plan and documents the Free/paid distinctions and limits in its current [Workers pricing](https://developers.cloudflare.com/workers/platform/pricing/) and [Workers limits](https://developers.cloudflare.com/workers/platform/limits/) references. The engine must not infer account-specific entitlements from public documentation alone.

### Live finding (2026-11-07): account add-ons are empty; zone evidence is required

The first post-SinglePage main run (**42328401696**) returned HTTP 200 with an empty account add-on list (empty_subscription_inventory_unverified). That response proves only that no account-level add-ons were returned; it does **not** prove that every domain is on Free. Cloudflare bills zone plans separately from add-on subscriptions. The new gate therefore checks the complete zone inventory and every zone's current subscription before it can allow remote work. Until a future read-only run positively verifies all add-ons and all zones, zero_cost_eligible=false and remote operations remain skipped. The empty add-on response is not a reason to relax the gate.

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
- Groq and Gemini remain disabled until their provider-specific free eligibility is proven. The separate `raawaa-image-route-check` workflow performs one real, bounded 1024×1024 Workers AI image inference (four steps) after the account/zone Free-plan gate; it is not a dry-run and it consumes free Workers AI neurons when successful. The image chain rejects every provider with a non-zero configured price and falls back only to local/offline generation. Its `data/image-ledger.jsonl` is runner-local and does not establish an account-wide daily quota across separate GitHub runs; the Cloudflare Free-plan limit and the account billing gate are the hard no-paid boundary. No paid provider fallback is permitted.

## Boundaries

- No Production D1 migrations or Production Worker deployment.
- Fixed Staging and Production identifiers are checked before any remote write.
- Temporary tokens are generated per run and revoked in cleanup steps.
- Live model inference is not part of Engine CI.
- Do not claim unattended Task Runner operation until two genuine `event=schedule` runs succeed on `main`; the current evidence is manual dispatch only.

## Verification

`scripts/check_workflow_dispatch_guards.py`, `tests/test_workflow_dispatch_guards.py`, and `tests/test_provider_workflow_security.py` enforce the automatic push/schedule triggers, main-ref restriction, all-zone scope attestation and mandatory zero-cost preflight dependencies.


## Control Worker deployment boundary — 2026-11-07

The authenticated Control Worker deployment workflow now requires both the existing explicit `[deploy-control-worker]` merge marker and a successful read-only Cloudflare subscription inventory preflight. The preflight runs before D1 migration checks, bearer verification and Worker deployment. Unknown, incomplete or HTTP 400 billing evidence fails closed; it is not treated as a Free-plan proof. No deployment is performed by this guard change.
