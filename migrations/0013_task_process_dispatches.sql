-- Durable, lease-fenced identity and bounded outcomes for supervised processes.
-- This is deliberately separate from task_model_invocations: subprocesses do
-- not use provider-route retry semantics and require their own outcome schema.
CREATE TABLE IF NOT EXISTS task_process_dispatches (
  dispatch_id TEXT PRIMARY KEY CHECK(length(dispatch_id)=32 AND dispatch_id NOT GLOB '*[^0-9a-f]*'),
  task_id TEXT NOT NULL REFERENCES tasks(id),
  step_key TEXT NOT NULL CHECK(step_key='supervised_process:v1'),
  operation TEXT NOT NULL CHECK(operation IN ('git_head_revision','python_platform_probe')),
  operation_version TEXT NOT NULL CHECK(operation_version='process-supervisor:v1'),
  operation_fingerprint TEXT NOT NULL CHECK(length(operation_fingerprint)=64 AND operation_fingerprint NOT GLOB '*[^0-9a-f]*'),
  lease_fingerprint TEXT NOT NULL CHECK(length(lease_fingerprint)=16 AND lease_fingerprint NOT GLOB '*[^0-9a-f]*'),
  status TEXT NOT NULL CHECK(status IN ('prepared','in_flight','ambiguous','settled')),
  outcome_version INTEGER,
  outcome_json TEXT,
  detail TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  started_at TEXT,
  settled_at TEXT,
  UNIQUE(task_id, step_key),
  CHECK (
    (status='settled' AND outcome_version=1 AND outcome_json IS NOT NULL) OR
    (status!='settled' AND outcome_version IS NULL AND outcome_json IS NULL)
  )
);
CREATE INDEX IF NOT EXISTS idx_task_process_dispatches_status
  ON task_process_dispatches(status, updated_at);
CREATE INDEX IF NOT EXISTS idx_task_process_dispatches_task
  ON task_process_dispatches(task_id, created_at);

INSERT OR IGNORE INTO schema_migrations(version) VALUES ('0013_task_process_dispatches');
