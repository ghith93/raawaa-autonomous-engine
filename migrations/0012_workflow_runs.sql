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
  -- SHA-256 over the normalized steps actually enqueued. plan_digest binds the
  -- run identity to a plan; steps_digest binds it to the content. A replay
  -- carrying the same plan_digest but different steps is a conflict, not a
  -- no-op, and it is the stored digest — not a preflight read — that decides.
  steps_digest TEXT NOT NULL,
  plan_version INTEGER NOT NULL DEFAULT 1,
  -- Only 'dispatched' exists. A run row is written inside the dispatch batch, so
  -- it becomes visible only once every step was enqueued; there is no failed row
  -- to write, because the batch that failed rolled back. Execution state belongs
  -- to the tasks, not here. Widening this CHECK later is a migration; narrowing
  -- it quietly is why it is stated explicitly.
  status TEXT NOT NULL CHECK(status = 'dispatched'),
  step_count INTEGER NOT NULL CHECK(step_count >= 1),
  entry_step_ids TEXT NOT NULL,
  -- Every step id in the run, so reconciliation can name each task without
  -- pattern-matching on ids (a LIKE prefix would treat '_' in a run id as a
  -- wildcard).
  step_ids TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_workflow_runs_created ON workflow_runs(created_at DESC);

INSERT OR IGNORE INTO schema_migrations(version) VALUES ('0012_workflow_runs');
