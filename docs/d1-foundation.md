# Cloudflare D1 foundation

## What is implemented

- A dependency-free HTTP client for Cloudflare's D1 Query API.
- Parameterized single-query and batch request support.
- A first additive schema migration covering tasks, audit events, operations,
  structured memory, and model usage.
- Unit tests that mock the HTTP boundary and verify bearer auth, parameter binding,
  health-check response validation, and secret redaction.

## Configuration

Set these GitHub Actions secrets in the repository:

- `CLOUDFLARE_API_TOKEN`
- `CLOUDFLARE_ACCOUNT_ID`
- `D1_DATABASE_ID`

Never print or commit the token. The client reads environment variables and does
not include authorization headers in error messages.

## Important architecture boundary

D1 is not a mounted SQLite file. The current TaskStore, OperationStore,
MemoryStore, and UsageLedger still use local `sqlite3`; this change does not
silently redirect them to D1. The REST API is appropriate for bounded
administrative/bootstrap queries, but Cloudflare recommends a Worker proxy for
application traffic because the REST API is subject to global API rate limits.

Before replacing local stores, implement explicit repository interfaces and a
D1-backed adapter. Prove atomic claim/lease fencing, idempotency, stale lease
recovery, and operation reconciliation against a disposable D1 database. Do not
assume a sequence of REST calls is one transaction.

## Migration

Review migration SQL in pull requests before merging. Once a migration change is merged to main, `.github/workflows/d1-auto-migrate.yml` automatically lists and applies the reviewed migrations to the pinned D1 database. The workflow is serialized, verifies the configured database ID/name, and runs only on main pushes that change migrations or the workflow itself. It does not run for ordinary code changes. Keep migrations additive and idempotent; a successful CI run is required before merge.

## Next steps

1. Automatic, serialized migration after reviewed changes merge to main is
   implemented in `.github/workflows/d1-auto-migrate.yml`.
2. Keep the manual migration workflow as a recovery/operations fallback.
3. Add disposable-database integration tests for D1 semantics.
3. Introduce storage protocols and D1 repositories without changing the local
   SQLite implementation used by existing unit tests.
4. Move routine application traffic behind an authenticated Worker + D1 binding.
5. Only then enable scheduled D1-backed task execution; keep dry-run policy and
   zero spend enforced.
