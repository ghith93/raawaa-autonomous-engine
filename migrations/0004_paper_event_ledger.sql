-- Durable, append-only paper-trading research events. No order execution is implied.
CREATE TABLE IF NOT EXISTS paper_candidate_heads (
  candidate_id TEXT PRIMARY KEY,
  sequence INTEGER NOT NULL DEFAULT 0 CHECK(sequence >= 0),
  event_hash TEXT NOT NULL DEFAULT '0000000000000000000000000000000000000000000000000000000000000000',
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS paper_events (
  event_id TEXT PRIMARY KEY,
  candidate_id TEXT NOT NULL,
  sequence INTEGER NOT NULL CHECK(sequence > 0),
  event_type TEXT NOT NULL CHECK(event_type IN (
    'candidate_detected','snapshot_captured','route_simulated','gas_scenario_evaluated',
    'paper_order_simulated','outcome_observed','rejected','expired'
  )),
  occurred_at TEXT NOT NULL,
  chain_id INTEGER,
  block_number INTEGER,
  block_hash TEXT,
  strategy_id TEXT,
  source_pool TEXT,
  destination_pool TEXT,
  token_in TEXT,
  token_out TEXT,
  gross_profit_raw TEXT,
  gas_used TEXT,
  gas_price_wei TEXT,
  gas_cost_profit_raw TEXT,
  net_profit_raw TEXT,
  payload_json TEXT NOT NULL,
  previous_hash TEXT NOT NULL,
  event_hash TEXT NOT NULL,
  UNIQUE(candidate_id, sequence),
  FOREIGN KEY(candidate_id) REFERENCES paper_candidate_heads(candidate_id)
);
CREATE INDEX IF NOT EXISTS idx_paper_events_candidate_sequence
  ON paper_events(candidate_id, sequence);

CREATE TABLE IF NOT EXISTS paper_event_checkpoints (
  candidate_id TEXT NOT NULL,
  sequence INTEGER NOT NULL CHECK(sequence > 0),
  event_id TEXT NOT NULL UNIQUE,
  event_hash TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY(candidate_id, sequence),
  FOREIGN KEY(candidate_id) REFERENCES paper_candidate_heads(candidate_id),
  FOREIGN KEY(event_id) REFERENCES paper_events(event_id)
);

CREATE TRIGGER IF NOT EXISTS paper_events_no_update
BEFORE UPDATE ON paper_events
BEGIN SELECT RAISE(ABORT, 'paper events are append-only'); END;
CREATE TRIGGER IF NOT EXISTS paper_events_no_delete
BEFORE DELETE ON paper_events
BEGIN SELECT RAISE(ABORT, 'paper events are append-only'); END;
CREATE TRIGGER IF NOT EXISTS paper_event_checkpoints_no_update
BEFORE UPDATE ON paper_event_checkpoints
BEGIN SELECT RAISE(ABORT, 'paper checkpoints are append-only'); END;
CREATE TRIGGER IF NOT EXISTS paper_event_checkpoints_no_delete
BEFORE DELETE ON paper_event_checkpoints
BEGIN SELECT RAISE(ABORT, 'paper checkpoints are append-only'); END;

INSERT OR IGNORE INTO schema_migrations(version) VALUES ('0004_paper_event_ledger');
