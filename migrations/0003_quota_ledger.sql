-- Durable, shared provider quota ledger. Additive migration.
-- GitHub Actions runners are ephemeral, so local SQLite quota state is lost
-- between scheduled runs. These tables make quota usage, reservations,
-- cooldowns and inference audit events survive across runs.
CREATE TABLE IF NOT EXISTS quota_usage (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  route_id TEXT NOT NULL,
  requests INTEGER NOT NULL CHECK(requests >= 0),
  tokens INTEGER NOT NULL CHECK(tokens >= 0),
  occurred_at TEXT NOT NULL,
  settled_at TEXT,
  task_id TEXT,
  idempotency_key TEXT
);
CREATE INDEX IF NOT EXISTS idx_quota_usage_route_time ON quota_usage(route_id, occurred_at);
CREATE INDEX IF NOT EXISTS idx_quota_usage_task ON quota_usage(task_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_quota_usage_idempotency
  ON quota_usage(idempotency_key) WHERE idempotency_key IS NOT NULL;

CREATE TABLE IF NOT EXISTS quota_cooldowns (
  route_id TEXT PRIMARY KEY,
  until_at TEXT NOT NULL,
  reason TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS quota_audit_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  event TEXT NOT NULL,
  route_id TEXT,
  task_kind TEXT,
  details_json TEXT NOT NULL,
  occurred_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_quota_audit_time ON quota_audit_events(occurred_at);

INSERT OR IGNORE INTO schema_migrations(version) VALUES ('0003_quota_ledger');
