# D1 Staging migration runbook

## Purpose

The dedicated `raawaa-engine-staging` database is the only remote target for automatic schema validation. It is separate from the production Worker and production D1 database.

## Automatic staging migration gate

The workflow `.github/workflows/d1-staging-migrate.yml` runs only after an `Engine CI` workflow run:

- has completed successfully;
- was triggered by a push to `main`;
- checked out the exact `head_sha` that passed CI.

Before applying anything, it parses both Wrangler configurations and verifies the exact approved staging database name and ID, the single `DB` binding, and that the staging ID/name do not appear in production configuration. It then lists pending migrations, applies pending migrations to Staging only, and lists the final state.

The workflow does not deploy the Worker, alter secrets, or access production D1. Pull-request CI runs do not trigger staging migrations. A failed CI run does not trigger them either.

## Current baseline

At the first successful remote inventory on 2026-11-03:

- D1 `SELECT 1` healthcheck succeeded against Staging.
- Wrangler reported migrations `0001_initial.sql` through `0005_telegram_webhook_receipts.sql` as pending.
- No migration had been applied at that checkpoint.

The first successful staging migration workflow run is the evidence required to mark those migrations applied. Do not infer schema readiness from the inventory-only workflow.

## Production remains separate

Production migration operations remain governed by [the production D1 migration runbook](d1-migration-runbook.md) and the manually dispatched `.github/workflows/d1-migrate.yml`. Never point the staging workflow at `wrangler.toml` or the production database ID.

## Failure handling

If staging migration fails:

1. Keep production untouched.
2. Inspect the exact workflow SHA and sanitized Wrangler error.
3. Check which migrations were recorded as applied; D1 applies migration files in order.
4. Fix the migration or workflow on a reviewed branch and rerun through a successful main CI push.
5. Do not manually mark migrations as applied or edit the production migration ledger.
