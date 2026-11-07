# Staging side-effect gate — 2026-11-07

## Required default

Ordinary pushes and merges to `main` run tests and read-only checks only. They must not automatically apply Staging D1 migrations, deploy the Staging Worker, install temporary secrets, publish telemetry, or run remote Staging proofs.

The Engine CI workflow now requires an explicit `workflow_dispatch` input named `enable_staging_operations` with `default: false` before any Staging-effect job can run. All four Staging jobs are guarded by both the main-branch condition and this explicit opt-in.

## Operational boundary

- Branch pushes and pull requests: offline tests only; Staging jobs skip.
- Main push/merge: tests only; Staging jobs skip.
- Manual dispatch with the default input: tests only; Staging jobs skip.
- Manual dispatch with `enable_staging_operations=true`: Staging operations may run against the separately validated Staging target only.
- Production D1 and Production Worker remain outside this workflow's Staging scope.

## Verification

The guard is enforced by `scripts/check_workflow_dispatch_guards.py` and regression-tested in `tests/test_workflow_dispatch_guards.py` and `tests/test_provider_workflow_security.py`. CI must pass on the exact PR head before merge.
