# Staging side-effect gate — 2026-11-07

## Incident record

Main commit `0b88adedc849f824e4be0d8a36bc54ead82bf817` triggered Engine CI run [42312921246](https://github.com/ghith93/raawaa-autonomous-engine/actions/runs/42312921246). The existing workflow automatically:
- applied pending migrations to the isolated Staging D1 database;
- deployed the Staging Worker;
- installed temporary Staging API, quota-admin and researcher-lane secrets;
- ran remote contention, lane-isolation, monetary-ledger and mobile-readback checks;
- wrote sanitized operational telemetry to Staging D1.

The workflow completed successfully. All temporary Staging secrets were deleted successfully, including the additional mobile-readback token. The proof logs recorded zero provider calls, zero external writes, USD 0 spend, and no Production access. The Groq workflow's eligibility job succeeded but its inference smoke job was skipped. No Production deployment or Production D1 access was observed.

Lane isolation also found two existing running `supervised_probe` tasks and intentionally left them untouched:
- `237660ed-2a97-415f-8a24-fc3e20f5f641`
- `0bb6b3cb-c16e-4c12-92c8-2602206c7937`

Do not recover or delete either task without exact-ID read-only inspection and ownership evidence.

## Required default

Ordinary pushes and merges to `main` must run tests and the read-only Graphify job only. They must not automatically migrate Staging D1, deploy the Staging Worker, install temporary secrets, write telemetry, or run remote Staging proofs.

The revised `.github/workflows/ci.yml` in PR #625:
- adds boolean `workflow_dispatch.inputs.enable_staging_operations`, required and default `false`;
- gates all four Staging-effect jobs on `github.ref == refs/heads/main`, `workflow_dispatch`, and `inputs.enable_staging_operations == true`;
- adds a deterministic guard in `scripts/check_workflow_dispatch_guards.py` and regression tests proving the gate fails closed.

This retains an explicit operator path for a future Staging run while making main pushes read-only by default. Do not dispatch it now. No Staging or Production writes/deployments are authorized by this change.

## Verification and merge gate

- Run the full Engine CI on the exact PR head.
- Verify the guard rejects removal of the opt-in or changing its default to true.
- Verify the Graphify job is read-only, has no secrets, and does not depend on Staging jobs.
- Independently review the workflow conditions and confirm no other main-push workflow writes to Staging.
- Do not merge if any Staging-effect job can run on push or if any temporary credential is not revoked.

This document records the Staging-only incident and the corrective gate; it does not authorize a future Staging dispatch.
