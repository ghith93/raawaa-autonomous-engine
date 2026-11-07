# Autonomous Staging lifecycle — 2026-11-07

## Default operation

- Engine CI runs on pushes, pull requests and daily at 04:17 UTC.
- Offline policy, Python, Worker, local D1 and local Workflows tests run on every CI event.
- A successful push to `main` and the daily schedule automatically start the Staging lifecycle only after a read-only Cloudflare billing preflight establishes eligibility.
- Engine CI exposes no `workflow_dispatch` trigger. No person needs to open Actions or manually start the routine lifecycle.
- The D1 Task Runner has its own five-minute schedule. Its `workflow_dispatch` path is an internal GitHub App/watchdog fallback for missed scheduled runs, not an owner-operated control. All remote Worker calls and task batches are gated by the same billing preflight.

## Zero-cost preflight

The preflight reads the Cloudflare account subscriptions endpoint only: `GET /accounts/{account_id}/subscriptions`. It follows every page and validates the complete inventory, pagination metadata, finite nonnegative prices, subscription states and plan identity.

Eligibility requires at least one active, explicitly named Free subscription and no active paid, trial, awaiting-payment or unknown subscription. Empty, inactive-only, incomplete, unavailable or malformed inventory is **not** proof of Free eligibility. The preflight publishes only a boolean and sanitized reason. HTTP diagnostics expose status codes only; response bodies, account identifiers and secrets are never logged.

This is a conservative gate for the account-level subscription inventory, not a global no-charge guarantee. It does not independently establish the billing posture of every Cloudflare product/zone, external provider account, or model route. Model-provider billing and Workers AI entitlements remain separate gates. If any required evidence is unknown, remote operations stay skipped.

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
- Groq, Gemini and Workers AI inference routes remain disabled. No paid fallback is permitted.

## Boundaries

- No Production D1 migrations or Production Worker deployment.
- Fixed Staging and Production identifiers are checked before any remote write.
- Temporary tokens are generated per run and revoked in cleanup steps.
- Live model inference is not part of Engine CI.
- No owner-operated manual dispatch is needed for routine CI or Task Runner scheduling.

## Verification

`scripts/check_workflow_dispatch_guards.py`, `tests/test_workflow_dispatch_guards.py`, and `tests/test_provider_workflow_security.py` enforce the automatic push/schedule triggers, main-ref restriction and mandatory zero-cost preflight dependencies.
