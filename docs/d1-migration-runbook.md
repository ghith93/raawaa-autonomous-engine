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

## Current known gate

Migration `0005_telegram_webhook_receipts` adds a receipt table and index and records its version in `schema_migrations`. Its presence is only schema readiness; it does not activate Telegram webhook delivery. Do not apply it until the complete pending migration sequence and production recovery requirements have been reviewed.
