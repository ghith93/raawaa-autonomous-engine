# Autonomous Staging lifecycle — 2026-11-07

## Default operation

- Engine CI runs on pushes, pull requests and daily at 04:17 UTC.
- Offline policy, Python, Worker, local D1 and local Workflows tests run without a human dispatch.
- A successful push to `main` automatically starts the Staging lifecycle only after a read-only Cloudflare billing preflight establishes that the account has no active paid subscriptions.
- The daily schedule runs the test and research-discovery cycles; it does not repeatedly deploy Staging when source code has not changed.
- Engine CI exposes no `workflow_dispatch` trigger. There is no routine manual start.

## Zero-cost preflight

The preflight reads the account subscriptions endpoint only. It fails closed if credentials are missing, the endpoint fails, response size/schema/pagination is incomplete, a subscription has an unknown state/price/plan, or any active paid subscription is found. The gate must validate the complete paginated result and finite nonnegative prices. It publishes only a boolean eligibility result and sanitized reason.

Cloudflare documents Workers Free as the default plan and documents that Workers Free request limits and D1 Free daily read/write limits fail closed rather than switching to paid usage. The Staging lifecycle uses only an isolated Worker and its separate D1 database; it does not create a paid service or activate model inference.

## Boundaries

- No Production D1 migrations or Production Worker deployment.
- Fixed Staging and Production identifiers are checked before any remote write.
- Temporary Staging tokens are generated per run and revoked in cleanup steps.
- Live model inference is not part of Engine CI. Groq smoke remains a separate bounded, Free-only workflow with its own hard gates.
- If zero-cost eligibility cannot be proven, all Staging side effects skip automatically; local CI and research discovery continue.
- No manual dispatch is needed or exposed for the Engine CI Staging lifecycle.

## Verification

`scripts/check_workflow_dispatch_guards.py`, `tests/test_workflow_dispatch_guards.py`, and `tests/test_provider_workflow_security.py` enforce the automatic push trigger, no manual dispatch, main-ref restriction and mandatory zero-cost preflight dependency.
