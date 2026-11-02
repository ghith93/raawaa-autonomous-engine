# Production D1 migration runbook

## Purpose

Inspect the production D1 migration ledger without changing the database. The inventory workflow is deliberately read-only and is the required first step before considering any production migration.

## Read-only inventory

1. Open [D1 Migration Inventory (Read Only)](../.github/workflows/d1-migration-inventory.yml) in GitHub Actions.
2. Run it from the reviewed `main` branch.
3. Inspect the complete `wrangler d1 migrations list` output and record the workflow run URL, checked-out commit SHA, applied migrations, and pending migrations.
4. Compare the pending list with the exact migration files on that SHA. Review the SQL, compatibility, data effects, and recovery/restore plan.

This workflow only runs `wrangler d1 migrations list --remote`. It does not invoke `migrations apply`, issue SQL writes, or alter Worker secrets.

## Production apply gate

The separate `.github/workflows/d1-migrate.yml` workflow is a production write operation. It currently lists remote migration status and then applies **all pending migrations** in the same run after its typed database confirmation.

Do not dispatch it merely to inspect status. Before a separate operator decision to apply:

- verify the exact reviewed `main` SHA;
- complete and retain the read-only inventory output;
- identify every pending migration, not only the newest one;
- review each migration in order and its dependencies;
- validate the same migration sequence against a non-production D1 database;
- prepare and verify a recovery/restore plan appropriate to the affected data;
- confirm the target database and accept the production impact.

If any item is unknown, stop after inventory. A successful Worker deployment or `/health` response does not prove schema readiness.

## Latest verified production checkpoint

As of 2026-11-02 23:45 UTC, the manual production migration workflow completed successfully on reviewed `main` SHA `e5eb80c395155151417ad82b761e0b7d5829ce6a`:

- [Apply D1 Migrations run #2](https://github.com/ghith93/raawaa-autonomous-engine/actions/runs/41547859981) completed with `success`.
- Wrangler reported both `0004_paper_event_ledger.sql` and `0005_telegram_webhook_receipts.sql` as applied in its final remote migration status output.
- The target was the remote D1 database `raawaa-autonomous-e`.

The Control Worker deployment workflow [run #10](https://github.com/ghith93/raawaa-autonomous-engine/actions/runs/41546403591) also completed successfully on commit `43b4f0d7ad2e3e63c10dd6f92fbebc00739610e7`. Subsequent unauthenticated production route checks returned HTTP 200 for `/health` and `/mobile`, and HTTP 401 for `/v1/mobile/status`, confirming the protected route is present and enforcing authentication. A post-deployment rerun of Task Runner run [#22, attempt 2](https://github.com/ghith93/raawaa-autonomous-engine/actions/runs/41540081196) then verified the authenticated D1 mobile-status route with HTTP 200: paper-event and provider-usage ledgers were available, pending/running tasks were both zero, and paper events were zero. The bounded batch ended `queue_idle` with zero tasks processed; the idempotent smoke enqueue returned `created=false` and an already-succeeded task. This verifies the authenticated read path and idle queue, not a newly processed task.

These are historical verification results, not a permanent guarantee of current state. Before any future production migration, run the read-only inventory again and compare the complete pending list with the exact reviewed SHA. The apply workflow still applies **all** pending migrations in one run.

Migration `0005_telegram_webhook_receipts` provides schema readiness only. It does not register or activate Telegram webhook delivery. Keep webhook activation separately gated by operator-managed secrets, private-chat allowlisting, and a deliberate configuration/deployment review.
