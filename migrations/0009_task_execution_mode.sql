-- Distinguish simulated task completion from a selected live-inference execution path.
-- Existing rows remain unknown; newly enqueued tasks start as not_started.
ALTER TABLE tasks ADD COLUMN execution_mode TEXT NOT NULL DEFAULT 'unknown'
  CHECK(execution_mode IN ('unknown','not_started','dry_run','live_inference'));
INSERT OR IGNORE INTO schema_migrations(version) VALUES ('0009_task_execution_mode');
