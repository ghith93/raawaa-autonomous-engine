-- Durable, provider-neutral monetary reservations for cloud execution.
-- All amounts are integer nano-USD; pending/ambiguous reservations remain budgeted.
CREATE TABLE IF NOT EXISTS monetary_budget_policies (
  policy_key TEXT PRIMARY KEY,
  daily_cap_nanos INTEGER NOT NULL CHECK(daily_cap_nanos >= 0),
  monthly_cap_nanos INTEGER NOT NULL CHECK(monthly_cap_nanos >= 0),
  per_task_cap_nanos INTEGER NOT NULL CHECK(per_task_cap_nanos >= 0),
  enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

INSERT OR IGNORE INTO monetary_budget_policies(
  policy_key,daily_cap_nanos,monthly_cap_nanos,per_task_cap_nanos,enabled
) VALUES('default',400000000,12000000000,200000000,1);

CREATE TABLE IF NOT EXISTS monetary_reservations (
  reservation_id TEXT PRIMARY KEY,
  policy_key TEXT NOT NULL REFERENCES monetary_budget_policies(policy_key),
  provider TEXT NOT NULL CHECK(length(provider) BETWEEN 1 AND 100),
  model TEXT NOT NULL CHECK(length(model) BETWEEN 1 AND 200),
  task_kind TEXT NOT NULL CHECK(length(task_kind) BETWEEN 1 AND 100),
  task_id TEXT NOT NULL CHECK(length(task_id) BETWEEN 1 AND 200),
  utc_day TEXT NOT NULL CHECK(length(utc_day)=10),
  utc_month TEXT NOT NULL CHECK(length(utc_month)=7),
  estimated_cost_nanos INTEGER NOT NULL CHECK(estimated_cost_nanos >= 0),
  status TEXT NOT NULL CHECK(status IN ('reserved','in_flight','ambiguous','settled','released')),
  provider_io_started_at TEXT,
  ambiguous_at TEXT,
  ambiguous_reason TEXT,
  actual_cost_nanos INTEGER CHECK(actual_cost_nanos IS NULL OR actual_cost_nanos >= 0),
  input_tokens INTEGER CHECK(input_tokens IS NULL OR input_tokens >= 0),
  output_tokens INTEGER CHECK(output_tokens IS NULL OR output_tokens >= 0),
  cost_basis TEXT CHECK(cost_basis IS NULL OR cost_basis IN ('provider_reported','verified_profile_estimate','verified_profile_floor','provider_rejected_429')),
  settled_at TEXT,
  released_at TEXT,
  release_reason TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  CHECK(
    (status='reserved' AND provider_io_started_at IS NULL AND settled_at IS NULL AND released_at IS NULL AND actual_cost_nanos IS NULL)
    OR (status='in_flight' AND provider_io_started_at IS NOT NULL AND settled_at IS NULL AND released_at IS NULL AND actual_cost_nanos IS NULL)
    OR (status='ambiguous' AND provider_io_started_at IS NOT NULL AND ambiguous_at IS NOT NULL AND settled_at IS NULL AND released_at IS NULL AND actual_cost_nanos IS NULL)
    OR (status='settled' AND provider_io_started_at IS NOT NULL AND settled_at IS NOT NULL AND released_at IS NULL AND actual_cost_nanos IS NOT NULL AND input_tokens IS NOT NULL AND output_tokens IS NOT NULL AND cost_basis IS NOT NULL)
    OR (status='released' AND provider_io_started_at IS NULL AND released_at IS NOT NULL AND settled_at IS NULL AND actual_cost_nanos IS NULL)
  )
);

CREATE INDEX IF NOT EXISTS idx_monetary_reservations_policy_day_status
  ON monetary_reservations(policy_key,utc_day,status);
CREATE INDEX IF NOT EXISTS idx_monetary_reservations_policy_month_status
  ON monetary_reservations(policy_key,utc_month,status);
CREATE INDEX IF NOT EXISTS idx_monetary_reservations_policy_task_status
  ON monetary_reservations(policy_key,task_id,status);
CREATE INDEX IF NOT EXISTS idx_monetary_reservations_policy_status
  ON monetary_reservations(policy_key,status);
