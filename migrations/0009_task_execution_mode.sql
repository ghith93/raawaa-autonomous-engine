-- Persist whether a task was simulated or selected the live-inference path.
-- Existing tasks are explicitly unknown/not-started rather than being inferred as live.
ALTER TABLE tasks ADD COLUMN execution_mode TEXT NOT NULL DEFAULT 'not_started'
  CHECK(execution_mode IN ('not_started','dry_run','live_inference'));
INSERT OR IGNORE INTO schema_migrations(version) VALUES ('0009_task_execution_mode');
