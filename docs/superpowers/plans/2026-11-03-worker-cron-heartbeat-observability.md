# Worker Cron Heartbeat Observability Implementation Plan

> **For agentic workers:** Execute each task with test-first changes and exact-head CI. The user has delegated implementation decisions and execution.

**Goal:** Make the existing Cloudflare Worker Cron execution visible in the authenticated mobile status surface without creating a new queue, migration, or paid dependency.

**Architecture:** Reuse the existing D1 `audit_log` as the canonical operational event ledger. The Worker's existing 15-minute `scheduled()` handler records its invocation and bounded stale-lease recovery counts; the read-only mobile status endpoint exposes the latest heartbeat and the dashboard renders it.

**Tech Stack:** Cloudflare Workers, D1, JavaScript, Node.js built-in test runner.

**Spec:** Existing operating contract in `docs/operations.md`, `docs/architecture.md`, and issue #34.

## Global Constraints

- No new D1 database, table, migration, queue, paid service, or dependency.
- Do not change the Python task consumer or claim Worker Cron executes Python tasks.
- Keep Telegram webhook, live inference, trading, and external writes disabled.
- Heartbeat records contain only cron metadata and bounded recovery counts; no secrets or user data.
- The mobile endpoint remains authenticated and read-only.

## Review Focus

- D1 write failure during scheduled execution must be visible as a failed cron invocation, not silently hidden.
- Empty task recovery must still produce a heartbeat with zero counts.
- Missing heartbeat history must be represented as unknown, not as proof the scheduler is down.
- The status endpoint must not query D1 before bearer authentication.
- The UI must render server values as text, never HTML.

---

### Task 1: Worker Cron heartbeat

**Files:**
- Modify: `tests/worker.test.js`
- Modify: `worker/index.js`

**Interfaces:**
- Consumes: `recoverStaleTasks(env) -> { requeued, failed }`
- Produces: `scheduled(controller, env)` writes an `audit_log` row with event `worker_cron_tick`, timestamp, cron expression, and recovery counts.

- [x] Add an assertion to the scheduled-handler test that verifies the audit row and bound metadata.
- [x] Run the focused Worker tests and confirm the new assertion fails before implementation (PR CI #1653).
- [x] Add the minimal audit insert after stale-lease recovery.
- [x] Re-run focused Worker tests.

### Task 2: Expose heartbeat in mobile status

**Files:**
- Modify: `tests/mobile_status.test.js`
- Modify: `worker/mobile-status.js`

**Interfaces:**
- Consumes: `audit_log(event='worker_cron_tick')`
- Produces: `worker_cron: { last_seen_at, cron, requeued, failed } | null`

- [x] Add tests for a recorded heartbeat and missing history.
- [x] Run focused mobile status tests and confirm the new assertions fail (PR CI #1653).
- [x] Query the latest heartbeat read-only and return normalized fields.
- [x] Add a visible dashboard row and a precise unavailable-metric message when no heartbeat exists.
- [x] Run focused Worker tests and the full repository CI suite (exact-head run #1657 pending final confirmation).

### Task 3: Review and integration

- [x] Review diff for authentication, query bounds, data minimization, and truthful unknown-state behavior.
- [ ] Require exact-head Engine CI success.
- [ ] Merge only after validation.
- [ ] Do not deploy production Worker until the merged artifact and deployment workflow are separately reviewed.
