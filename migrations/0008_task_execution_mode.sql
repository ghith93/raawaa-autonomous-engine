-- Record the execution path used when a task is claimed.
-- Legacy rows remain explicitly unknown; do not infer that historical successes were live execution.
ALTER TABLE tasks ADD COLUMN execution_mode TEXT NOT NULL DEFAULT 'unknown'
  CHECK(execution_mode IN ('unknown','dry_run','live_inference','codespace'));
CREATE INDEX IF NOT EXISTS idx_tasks_execution_mode_status
  ON tasks(execution_mode,status);

INSERT OR IGNORE INTO schema_migrations(version) VALUES ('0008_task_execution_mode');
