# Autonomous Staging lifecycle — 2026-11-07

## Default operation

- Engine CI runs on pushes, pull requests and daily at 04:17 UTC.
- Offline policy, Python, Worker, local D1 and local Workflows tests run on every CI event.
- A successful push to `main` and the daily schedule automatically start the Staging lifecycle only after a read-only Cloudflare billing preflight establishes that the account has no active paid subscriptions.
- Engine CI exposes no `workflow_dispatch` trigger. No person needs to open Actions or manually start the routine lifecycle.

## Zero-cost preflight

The preflight reads the Cloudflare account subscriptions endpoint only. It fails closed if credentials are missing, the endpoint fails, response size/schema/pagination is incomplete, a subscription has an unknown state/price/plan, or any active paid subscription is found. It follows every page and validates finite nonnegative prices and explicit Free plan identity. It publishes only a boolean eligibility result and sanitized reason.

Cloudflare documents Workers Free as the default plan and documents that Workers Free request limits and D1 Free daily read/write limits fail closed rather than switching to paid usage. The Staging lifecycle uses only an isolated Worker and its separate D1 database; it does not create a paid service or activate model inference.

## Automated Staging lifecycle

When the zero-cost preflight passes, the workflow:
1. Validates Staging and Production D1 identities are distinct and match the reviewed targets.
2. Applies pending migrations to Staging only.
3. Deploys the isolated Staging Worker on `workers.dev` only.
4. Generates ephemeral consumer/admin capabilities, verifies the Worker and researcher lane, and revokes temporary secrets on exit.
5. Verifies the monetary ledger and lane isolation.
6. Publishes sanitized operational telemetry to Staging D1 and verifies authenticated mobile status.

If zero-cost eligibility cannot be proven, all Staging side effects skip automatically while local tests and research discovery continue.

## Boundaries

- No Production D1 migrations or Production Worker deployment.
- Fixed Staging and Production identifiers are checked before any remote write.
- Temporary tokens are generated per run and revoked in cleanup steps.
- Live model inference is not part of Engine CI. Groq smoke remains a separate bounded, Free-only workflow with its own hard gates.
- No manual dispatch is needed or exposed for the Engine CI Staging lifecycle.

## Verification

`scripts/check_workflow_dispatch_guards.py`, `tests/test_workflow_dispatch_guards.py`, and `tests/test_provider_workflow_security.py` enforce the automatic push/schedule triggers, no manual dispatch, main-ref restriction and mandatory zero-cost preflight dependency.
