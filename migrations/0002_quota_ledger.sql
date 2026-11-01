-- Durable provider quota reservations shared by all scheduled workers.
-- Additive migration; never drops or rewrites existing task/account data.
CREATE TABLE IF NOT EXISTS quota_usage (
  id TEXT PRIMARY KEY,
  route_id TEXT NOT NULL,
  requests INTEGER NOT NULL CHECK(requests > 0),
  tokens INTEGER NOT NULL CHECK(tokens >= 0),
  occurred_at TEXT NOT NULL,
  settled_at TEXT,
  task_id TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_quota_usage_route_time
  ON quota_usage(route_id, occurred_at);
CREATE INDEX IF NOT EXISTS idx_quota_usage_task
  ON quota_usage(task_id, occurred_at);

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
CREATE INDEX IF NOT EXISTS idx_quota_audit_created
  ON quota_audit_events(occurred_at);

INSERT OR IGNORE INTO schema_migrations(version) VALUES ('0002_quota_ledger');
