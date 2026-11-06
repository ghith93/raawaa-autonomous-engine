-- Persist explicit ownership for generic and specialist task consumers.
-- Existing tasks remain on the general lane; no implicit rerouting occurs.
ALTER TABLE tasks ADD COLUMN execution_lane TEXT NOT NULL DEFAULT 'general'
  CHECK(execution_lane IN ('general','specialist:researcher','specialist:reviewer','specialist:marketer','specialist:operator','specialist:mobile_tester'));
CREATE INDEX idx_tasks_lane_status_priority
  ON tasks(execution_lane,status,priority DESC,created_at,id);
INSERT OR IGNORE INTO schema_migrations(version) VALUES ('0014_task_execution_lanes');
