-- Durable record of a dispatched workflow run.
--
-- Before this table a plan could be validated (engine/orchestrator.py) and a
-- manifest could be derived (engine/workflow_manifest.py), but nothing recorded
-- that a run had been dispatched: the queue held tasks and the plan lived in a
-- caller's memory. That made "did this run enqueue everything?" unanswerable,
-- and a partial dispatch indistinguishable from a complete one.
--
-- Rows are written in the same D1 batch as their tasks, so a row exists only if
-- every step was enqueued. There is no 'dispatching' state to reconcile.
CREATE TABLE IF NOT EXISTS workflow_runs (
  run_id TEXT PRIMARY KEY,
  workflow_id TEXT NOT NULL,
  plan_digest TEXT NOT NULL,
  plan_version INTEGER NOT NULL DEFAULT 1,
  status TEXT NOT NULL CHECK(status IN ('dispatched','dispatch_failed')),
  step_count INTEGER NOT NULL CHECK(step_count >= 1),
  entry_step_ids TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_workflow_runs_created ON workflow_runs(created_at DESC);

INSERT OR IGNORE INTO schema_migrations(version) VALUES ('0012_workflow_runs');
