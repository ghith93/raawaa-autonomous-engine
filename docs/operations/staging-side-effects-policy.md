# Staging lifecycle automation — 2026-11-07

## Default autonomous operation

Engine CI runs its test suite on pushes, pull requests and daily schedules. Its
four Staging-effect jobs skip these events, including main pushes. This is a
statement about Engine CI jobs only: other workflows have their own triggers and
may have side effects. GitHub Actions does not wake an Arena session.

## Manual dispatch safety

The Staging lifecycle runs only on a main-ref `workflow_dispatch` with
`enable_staging_operations=true`; the input defaults to false. When enabled, it
can apply migrations, deploy the isolated Worker, install temporary capabilities,
run remote proofs and publish telemetry. Opt-in is not evidence of a zero-cost
boundary: verify provider billing and all other safety requirements before use.

## Boundaries

- No Production D1 migrations or Production Worker deployment are part of this Staging lifecycle.
- The workflow verifies fixed Staging/Production identifiers before writes.
- Temporary tokens are generated per run and revoked in `always()` cleanup steps.
- No paid inference is enabled by these deployment checks; live inference and provider spending remain controlled by separate explicit policy/quota gates.
- CI failures remain visible and prevent dependent operations.

## Verification

`scripts/check_workflow_dispatch_guards.py`, `tests/test_workflow_dispatch_guards.py`, and `tests/test_provider_workflow_security.py` check the manual opt-in and Engine CI event guards.


## Autonomous CI policy — 2026-11-07

Engine CI runs automatically on branch pushes, pull requests, and once daily at 04:17 UTC. These runs execute the full offline test, Worker, local D1, and policy suites without requiring a person to dispatch them.

Staging migrations, Worker deployment, temporary secret installation, telemetry writes, and remote proofs are deliberately excluded from push and scheduled runs. They remain behind the existing explicit manual opt-in as an emergency/diagnostic path only; ordinary autonomous progress does not depend on it. The scheduled research-discovery workflow remains independent and runs daily. No Arena session wake is implied by a GitHub Actions run.
