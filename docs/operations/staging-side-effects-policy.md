# Staging lifecycle automation — zero-spend policy (2026-11-07)

## Default autonomous operation

A push to `main` and the daily 04:17 UTC schedule automatically run Engine CI tests, followed by a read-only Cloudflare subscription preflight. If the preflight proves there are no active paid subscriptions and all subscription pricing is known, the isolated Staging lifecycle proceeds automatically:
1. Validate Staging and Production D1 identities are distinct and match the reviewed targets.
2. Apply pending migrations to Staging only.
3. Deploy the isolated Staging Worker on `workers.dev` only.
4. Generate ephemeral consumer/admin capabilities, verify the worker and researcher lane, and revoke temporary secrets on exit.
5. Verify the monetary ledger and lane isolation.
6. Publish sanitized operational telemetry to Staging D1 and verify authenticated mobile status.

No person needs to open Actions or manually start this lifecycle. There is no routine `workflow_dispatch` trigger.

## Fail-closed billing preflight

The read-only preflight queries the Cloudflare account subscriptions endpoint using a step-scoped token. It publishes only a boolean output.

Staging operations are blocked and Engine CI fails if:
- Billing Read credentials are missing or the subscriptions endpoint is unavailable.
- The response is malformed or contains a non-object subscription record.
- Any active paid subscription exists.
- An active subscription has unknown pricing.

The check does not depend on the account spend-policy endpoint; a 404 is not treated as a spend hard-stop. Workers Free is Cloudflare's default plan; Workers Paid starts at $5/month. On Workers Free, usage above limits fails rather than creating overage charges; D1 Free daily row-limit excess returns errors until midnight UTC.

## Boundaries

- No Production D1 migrations or Production Worker deployment are part of this Staging lifecycle.
- The workflow verifies fixed Staging/Production identifiers before writes.
- Temporary tokens are generated per run and revoked in `always()` cleanup steps.
- Paid inference and provider spending remain disabled unless separately approved by explicit quota policy; the zero-cost preflight only governs Cloudflare Staging operations.
- Branch pushes and pull requests run tests only; they never receive Cloudflare billing credentials or execute Staging side effects.
- If the preflight fails, the downstream Staging jobs and the dependent Groq Staging smoke workflow do not run.

## Verification

`scripts/verify_cloudflare_zero_cost_boundary.py` implements the read-only fail-closed check. `scripts/check_workflow_dispatch_guards.py`, `tests/test_workflow_dispatch_guards.py`, `tests/test_provider_workflow_security.py`, and `tests/test_cloudflare_zero_cost_boundary.py` enforce the automatic path, subscription checks, and fail-closed behavior.
