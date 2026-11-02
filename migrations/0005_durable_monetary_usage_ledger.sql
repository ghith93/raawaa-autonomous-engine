-- Durable, idempotent monetary usage reservations and settlement.
-- Additive only; do not apply to production D1 without explicit deployment approval.
ALTER TABLE model_usage ADD COLUMN cost_nanos INTEGER NOT NULL DEFAULT 0;
ALTER TABLE model_usage ADD COLUMN task_id TEXT;
ALTER TABLE model_usage ADD COLUMN reservation_id INTEGER;
UPDATE model_usage
SET cost_nanos=CAST(ROUND(cost_usd * 1000000000) AS INTEGER)
WHERE cost_usd > 0;

CREATE UNIQUE INDEX IF NOT EXISTS idx_model_usage_reservation
  ON model_usage(reservation_id);
CREATE INDEX IF NOT EXISTS idx_model_usage_task
  ON model_usage(task_id, created_at);

CREATE TABLE IF NOT EXISTS model_usage_reservations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  provider TEXT NOT NULL,
  model TEXT NOT NULL,
  task_kind TEXT NOT NULL,
  task_id TEXT,
  created_at TEXT NOT NULL,
  estimated_cost_nanos INTEGER NOT NULL CHECK(estimated_cost_nanos >= 0),
  settled_at TEXT,
  actual_cost_nanos INTEGER,
  input_tokens INTEGER,
  output_tokens INTEGER
);
CREATE INDEX IF NOT EXISTS idx_model_usage_reservations_created
  ON model_usage_reservations(created_at, settled_at);
CREATE INDEX IF NOT EXISTS idx_model_usage_reservations_task
  ON model_usage_reservations(task_id, settled_at);

INSERT OR IGNORE INTO schema_migrations(version) VALUES ('0005_durable_monetary_usage_ledger');
