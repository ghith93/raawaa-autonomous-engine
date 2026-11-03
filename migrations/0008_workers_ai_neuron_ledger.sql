-- Workers AI Neuron reservation ledger. This is accounting only: no AI binding or inference route.
-- The first pilot is pinned to Gemma 4 26B A4B and a hard 10,000-Neuron UTC-day ceiling.
CREATE TABLE IF NOT EXISTS workers_ai_neuron_policy (
  policy_key TEXT PRIMARY KEY CHECK(policy_key = 'default'),
  daily_cap_neurons INTEGER NOT NULL CHECK(daily_cap_neurons = 10000),
  allowed_model_id TEXT NOT NULL CHECK(allowed_model_id = '@cf/google/gemma-4-26b-a4b-it'),
  enabled INTEGER NOT NULL DEFAULT 0 CHECK(enabled IN (0,1)),
  updated_at TEXT NOT NULL
);
INSERT OR IGNORE INTO workers_ai_neuron_policy(
  policy_key,daily_cap_neurons,allowed_model_id,enabled,updated_at
) VALUES(
  'default',10000,'@cf/google/gemma-4-26b-a4b-it',0,CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS workers_ai_neuron_reservations (
  reservation_id TEXT PRIMARY KEY,
  idempotency_key TEXT NOT NULL UNIQUE,
  model_id TEXT NOT NULL CHECK(model_id = '@cf/google/gemma-4-26b-a4b-it'),
  task_kind TEXT NOT NULL CHECK(task_kind = 'research_brief'),
  task_id TEXT NOT NULL,
  data_classification TEXT NOT NULL CHECK(data_classification = 'public'),
  status TEXT NOT NULL CHECK(status IN ('reserved','in_flight','ambiguous','settled','released')),
  estimated_neurons INTEGER NOT NULL CHECK(estimated_neurons > 0),
  actual_neurons INTEGER CHECK(actual_neurons IS NULL OR actual_neurons >= 0),
  day_utc TEXT NOT NULL,
  created_at TEXT NOT NULL,
  started_at TEXT,
  settled_at TEXT,
  released_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_workers_ai_neuron_reservations_day_status
  ON workers_ai_neuron_reservations(day_utc,status);
CREATE INDEX IF NOT EXISTS idx_workers_ai_neuron_reservations_task
  ON workers_ai_neuron_reservations(task_id,status,created_at);

CREATE TABLE IF NOT EXISTS workers_ai_neuron_quarantines (
  model_id TEXT PRIMARY KEY,
  reason TEXT NOT NULL,
  reservation_id TEXT NOT NULL,
  quarantined_at TEXT NOT NULL
);

INSERT OR IGNORE INTO schema_migrations(version)
VALUES ('0008_workers_ai_neuron_ledger');
