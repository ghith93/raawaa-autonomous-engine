-- Durable logical model-call identity, independent of provider route.
CREATE TABLE IF NOT EXISTS task_model_invocations (
  invocation_id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL REFERENCES tasks(id),
  step_key TEXT NOT NULL,
  task_kind TEXT NOT NULL,
  request_id TEXT NOT NULL UNIQUE,
  status TEXT NOT NULL CHECK(status IN ('prepared','in_flight','ambiguous','settled')),
  last_route_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  started_at TEXT,
  settled_at TEXT,
  UNIQUE(task_id, step_key)
);
CREATE INDEX IF NOT EXISTS idx_task_model_invocations_task
  ON task_model_invocations(task_id, created_at);
CREATE INDEX IF NOT EXISTS idx_task_model_invocations_status
  ON task_model_invocations(status, updated_at);

CREATE TABLE IF NOT EXISTS task_model_invocation_attempts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  invocation_id TEXT NOT NULL REFERENCES task_model_invocations(invocation_id),
  route_id TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('in_flight','rejected','ambiguous','settled')),
  rejection_code TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(invocation_id, route_id)
);
CREATE INDEX IF NOT EXISTS idx_invocation_attempts_status
  ON task_model_invocation_attempts(invocation_id, status);

INSERT OR IGNORE INTO schema_migrations(version) VALUES ('0007_task_model_invocations');
