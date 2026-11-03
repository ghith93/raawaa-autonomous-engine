-- Extend the persistent research store to the metadata-only arXiv and SearXNG sources.
-- Rebuild the table to widen its source CHECK constraint while preserving all review history.
CREATE TABLE research_candidates_new (
  source_key TEXT PRIMARY KEY,
  source TEXT NOT NULL CHECK(source IN ('github','crossref','arxiv','searxng')),
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

INSERT INTO research_candidates_new (
  source_key, source, title, url, metadata_json, review_status, review_note,
  first_seen_at, last_seen_at, reviewed_at, discovery_count, updated_at
)
SELECT
  source_key, source, title, url, metadata_json, review_status, review_note,
  first_seen_at, last_seen_at, reviewed_at, discovery_count, updated_at
FROM research_candidates;

DROP TABLE research_candidates;
ALTER TABLE research_candidates_new RENAME TO research_candidates;

CREATE INDEX IF NOT EXISTS idx_research_review_last_seen
  ON research_candidates(review_status, last_seen_at DESC);
CREATE INDEX IF NOT EXISTS idx_research_source_last_seen
  ON research_candidates(source, last_seen_at DESC);

INSERT OR IGNORE INTO schema_migrations(version) VALUES ('0008_research_source_expansion');
