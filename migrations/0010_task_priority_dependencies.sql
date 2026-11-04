-- Persist deterministic task priority and DAG dependencies.
-- Higher priority is claimed first; ties retain FIFO creation order.
-- Dependencies are JSON arrays of existing task IDs.
ALTER TABLE tasks ADD COLUMN priority INTEGER NOT NULL DEFAULT 0
  CHECK(priority BETWEEN -100 AND 100);
ALTER TABLE tasks ADD COLUMN depends_on TEXT NOT NULL DEFAULT '[]'
  CHECK(json_valid(depends_on));
CREATE INDEX IF NOT EXISTS idx_tasks_claim_priority
  ON tasks(status,priority DESC,created_at,id);

INSERT OR IGNORE INTO schema_migrations(version) VALUES ('0010_task_priority_dependencies');
