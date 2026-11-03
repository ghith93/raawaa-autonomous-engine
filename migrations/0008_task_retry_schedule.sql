-- Add durable eligibility time for safe delayed task retries.
-- Existing tasks remain immediately eligible because NULL means no delay.
ALTER TABLE tasks ADD COLUMN next_attempt_at TEXT;
CREATE INDEX IF NOT EXISTS idx_tasks_due ON tasks(status,next_attempt_at,created_at);
INSERT OR IGNORE INTO schema_migrations(version) VALUES ('0008_task_retry_schedule');
