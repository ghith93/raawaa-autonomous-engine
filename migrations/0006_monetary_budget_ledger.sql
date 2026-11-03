-- Durable, provider-agnostic monetary budget reservations and settlements.
-- All amounts are integer nano-USD; unresolved reservations remain charged across windows.
CREATE TABLE IF NOT EXISTS monetary_reservations (
  reservation_id TEXT PRIMARY KEY,
  idempotency_key TEXT NOT NULL UNIQUE,
  provider TEXT NOT NULL,
  model TEXT NOT NULL,
  task_kind TEXT NOT NULL,
  task_id TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('reserved','in_flight','ambiguous','settled','released')),
  estimated_cost_nanos INTEGER NOT NULL CHECK(estimated_cost_nanos >= 0),
  actual_cost_nanos INTEGER CHECK(actual_cost_nanos IS NULL OR actual_cost_nanos >= 0),
  daily_budget_nanos INTEGER NOT NULL CHECK(daily_budget_nanos >= 0),
  monthly_budget_nanos INTEGER NOT NULL CHECK(monthly_budget_nanos >= 0),
  per_task_budget_nanos INTEGER NOT NULL CHECK(per_task_budget_nanos >= 0),
  input_tokens INTEGER CHECK(input_tokens IS NULL OR input_tokens >= 0),
  output_tokens INTEGER CHECK(output_tokens IS NULL OR output_tokens >= 0),
  created_at TEXT NOT NULL,
  started_at TEXT,
  settled_at TEXT,
  released_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_monetary_reservations_status_created
  ON monetary_reservations(status, created_at);
CREATE INDEX IF NOT EXISTS idx_monetary_reservations_settled
  ON monetary_reservations(status, settled_at);
CREATE INDEX IF NOT EXISTS idx_monetary_reservations_task
  ON monetary_reservations(task_id, status, settled_at);

CREATE TABLE IF NOT EXISTS monetary_route_quarantines (
  route_id TEXT PRIMARY KEY,
  reason TEXT NOT NULL,
  reservation_id TEXT NOT NULL,
  quarantined_at TEXT NOT NULL
);

INSERT OR IGNORE INTO schema_migrations(version) VALUES ('0006_monetary_budget_ledger');
