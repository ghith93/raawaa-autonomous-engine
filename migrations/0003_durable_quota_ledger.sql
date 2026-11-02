-- Durable provider quota ledger. Additive: preserves existing task and audit tables.
CREATE TABLE IF NOT EXISTS quota_usage (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  route_id TEXT NOT NULL,
  requests INTEGER NOT NULL DEFAULT 1 CHECK(requests = 1),
  tokens INTEGER NOT NULL CHECK(tokens >= 0),
  occurred_at TEXT NOT NULL,
  settled_at TEXT,
  task_id TEXT,
  settlement_key TEXT
);
CREATE INDEX IF NOT EXISTS idx_quota_usage_route_time
  ON quota_usage(route_id, occurred_at);
CREATE INDEX IF NOT EXISTS idx_quota_usage_task
  ON quota_usage(task_id, occurred_at);
CREATE INDEX IF NOT EXISTS idx_quota_usage_settlement
  ON quota_usage(id, settled_at);

CREATE TABLE IF NOT EXISTS quota_cooldowns (
  route_id TEXT PRIMARY KEY,
  until_at TEXT NOT NULL,
  reason TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS quota_audit_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  event TEXT NOT NULL,
  route_id TEXT,
  task_kind TEXT,
  details_json TEXT NOT NULL,
  occurred_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_quota_audit_events_time
  ON quota_audit_events(occurred_at);

INSERT OR IGNORE INTO schema_migrations(version) VALUES ('0003_durable_quota_ledger');
