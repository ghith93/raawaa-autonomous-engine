-- Durable, provider-agnostic monetary budget reservations and settlements.
-- All amounts are integer nano-USD; unresolved reservations remain charged across windows.
-- The Worker API cannot raise these operator-approved hard caps per request.
CREATE TABLE IF NOT EXISTS monetary_budget_policy (
  policy_key TEXT PRIMARY KEY CHECK(policy_key = 'default'),
  daily_budget_nanos INTEGER NOT NULL CHECK(daily_budget_nanos >= 0),
  monthly_budget_nanos INTEGER NOT NULL CHECK(monthly_budget_nanos >= 0),
  per_task_budget_nanos INTEGER NOT NULL CHECK(per_task_budget_nanos >= 0),
  enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)),
  updated_at TEXT NOT NULL
);
INSERT OR IGNORE INTO monetary_budget_policy(
  policy_key,daily_budget_nanos,monthly_budget_nanos,per_task_budget_nanos,enabled,updated_at
) VALUES('default',400000000,12000000000,200000000,1,CURRENT_TIMESTAMP);

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

-- Preserve any already-recorded provider spend when moving from the legacy
-- model_usage table (which has no task attribution) into the durable ledger.
INSERT OR IGNORE INTO monetary_reservations (
  reservation_id,idempotency_key,provider,model,task_kind,task_id,status,
  estimated_cost_nanos,actual_cost_nanos,daily_budget_nanos,monthly_budget_nanos,
  per_task_budget_nanos,input_tokens,output_tokens,created_at,started_at,settled_at
)
SELECT
  'legacy-model-usage:' || id,
  'legacy-model-usage:' || id,
  provider,model,task_kind,'legacy-unattributed','settled',
  CAST(ROUND(cost_usd * 1000000000) AS INTEGER),
  CAST(ROUND(cost_usd * 1000000000) AS INTEGER),
  400000000,12000000000,200000000,
  input_tokens,output_tokens,created_at,created_at,created_at
FROM model_usage
WHERE cost_usd > 0;

INSERT OR IGNORE INTO schema_migrations(version) VALUES ('0006_monetary_budget_ledger');
