-- Persistent, deduplicated research candidate memory.
-- Candidate metadata is public-source metadata only; review decisions are preserved on rediscovery.
CREATE TABLE IF NOT EXISTS research_candidates (
  source_key TEXT PRIMARY KEY,
  source TEXT NOT NULL CHECK(source IN ('github','crossref')),
  title TEXT NOT NULL,
  url TEXT NOT NULL,
  metadata_json TEXT NOT NULL,
  review_status TEXT NOT NULL DEFAULT 'unreviewed'
    CHECK(review_status IN ('unreviewed','accepted','rejected','deferred')),
  review_note TEXT,
  first_seen_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  reviewed_at TEXT,
  discovery_count INTEGER NOT NULL DEFAULT 1 CHECK(discovery_count >= 1),
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_research_review_last_seen
  ON research_candidates(review_status, last_seen_at DESC);
CREATE INDEX IF NOT EXISTS idx_research_source_last_seen
  ON research_candidates(source, last_seen_at DESC);

INSERT OR IGNORE INTO schema_migrations(version) VALUES ('0002_research_candidates');
