# Staging lifecycle automation — 2026-11-07

## Default autonomous operation

A reviewed merge to `main` automatically triggers the complete Staging lifecycle after CI tests pass:
1. Validate Staging and Production D1 identities are distinct and match the reviewed targets.
2. Apply pending migrations to Staging only.
3. Deploy the isolated Staging Worker on `workers.dev` only.
4. Generate ephemeral consumer/admin capabilities, verify the worker and researcher lane, and revoke temporary secrets on exit.
5. Verify the monetary ledger and lane isolation.
6. Publish sanitized operational telemetry to Staging D1 and verify authenticated mobile status.

No person needs to open Actions or manually start this lifecycle. Failed tests block downstream Staging operations. Branch pushes and pull requests run code tests only; Staging jobs are main-only.

## Manual dispatch safety

The optional `workflow_dispatch` path remains available only with `enable_staging_operations=true`. Its input defaults to `false`, so a casual/manual dispatch cannot write to Staging accidentally. This is an additional safe manual path, not a requirement for normal operation.

## Boundaries

- No Production D1 migrations or Production Worker deployment are part of this Staging lifecycle.
- The workflow verifies fixed Staging/Production identifiers before writes.
- Temporary tokens are generated per run and revoked in `always()` cleanup steps.
- No paid inference is enabled by these deployment checks; live inference and provider spending remain controlled by separate explicit policy/quota gates.
- CI failures remain visible and prevent dependent operations.

## Verification

`scripts/check_workflow_dispatch_guards.py`, `tests/test_workflow_dispatch_guards.py`, and `tests/test_provider_workflow_security.py` enforce the automatic main-push path and false-by-default manual path.
