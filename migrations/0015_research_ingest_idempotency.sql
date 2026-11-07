-- Durable idempotency for public research discovery batches.
-- Replayed workflow attempts cannot inflate discovery_count; changed payloads
-- under an existing batch ID are rejected by the Worker.
CREATE TABLE IF NOT EXISTS research_ingest_batches (
  batch_id TEXT PRIMARY KEY,
  payload_sha256 TEXT NOT NULL CHECK(length(payload_sha256) = 64),
  candidate_count INTEGER NOT NULL CHECK(candidate_count BETWEEN 1 AND 25),
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS research_ingest_events (
  batch_id TEXT NOT NULL,
  source_key TEXT NOT NULL,
  payload_sha256 TEXT NOT NULL CHECK(length(payload_sha256) = 64),
  created_at TEXT NOT NULL,
  PRIMARY KEY(batch_id, source_key)
);

CREATE INDEX IF NOT EXISTS idx_research_ingest_events_source
  ON research_ingest_events(source_key, created_at DESC);

INSERT OR IGNORE INTO schema_migrations(version) VALUES ('0015_research_ingest_idempotency');
