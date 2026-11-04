-- Add deterministic queue priority and normalized dependency edges.
-- Existing tasks keep FIFO-equivalent priority 50 and have no dependencies.
ALTER TABLE tasks ADD COLUMN priority INTEGER NOT NULL DEFAULT 50
  CHECK(priority BETWEEN 0 AND 100);
ALTER TABLE tasks ADD COLUMN dependencies_json TEXT NOT NULL DEFAULT '[]';
CREATE TABLE task_dependencies (
  task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  dependency_task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE RESTRICT,
  PRIMARY KEY(task_id, dependency_task_id),
  CHECK(task_id <> dependency_task_id)
);
CREATE INDEX idx_task_dependencies_dependency
  ON task_dependencies(dependency_task_id, task_id);
CREATE INDEX idx_tasks_status_priority_created
  ON tasks(status, priority DESC, created_at, id);
INSERT OR IGNORE INTO schema_migrations(version)
VALUES ('0010_task_priority_dependencies');
