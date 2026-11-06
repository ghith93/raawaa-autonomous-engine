-- Isolated Multi-Store Commerce Spine for Cloudflare D1 (`raawaa-commerce-db`).
-- Kept in `commerce-migrations/` so the autonomous control plane (`migrations/`)
-- never mixes internal agent task queues with public customer checkout PII.

CREATE TABLE IF NOT EXISTS commerce_orders (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  store_id TEXT NOT NULL,
  order_ref TEXT NOT NULL UNIQUE,
  idempotency_key TEXT NOT NULL UNIQUE,
  state TEXT NOT NULL DEFAULT 'requested'
    CHECK (state IN ('requested', 'confirmed', 'prepared', 'shipped', 'delivered', 'refused', 'cancelled')),
  customer_name TEXT NOT NULL,
  customer_phone TEXT NOT NULL,
  customer_phone_hash TEXT NOT NULL,
  zone_id TEXT NOT NULL,
  zone_label_ar TEXT NOT NULL,
  address_detail TEXT NOT NULL DEFAULT '',
  payment_method TEXT NOT NULL
    CHECK (payment_method IN ('cod', 'sham_cash', 'syriatel_cash', 'mtn_cash')),
  wallet_transfer_ref TEXT NOT NULL DEFAULT '',
  scent TEXT NOT NULL DEFAULT '',
  notes TEXT NOT NULL DEFAULT '',
  lines_json TEXT NOT NULL,
  currency TEXT NOT NULL DEFAULT 'USD',
  subtotal_cents INTEGER NOT NULL CHECK (subtotal_cents >= 0),
  discount_cents INTEGER NOT NULL DEFAULT 0 CHECK (discount_cents >= 0),
  delivery_cents INTEGER NOT NULL DEFAULT 0 CHECK (delivery_cents >= 0),
  total_cents INTEGER NOT NULL CHECK (total_cents >= 0),
  unit_cost_cents INTEGER NOT NULL DEFAULT 0 CHECK (unit_cost_cents >= 0),
  courier_cost_cents INTEGER NOT NULL DEFAULT 200 CHECK (courier_cost_cents >= 0),
  refusal_cost_cents INTEGER NOT NULL DEFAULT 100 CHECK (refusal_cost_cents >= 0),
  spoilage_bps INTEGER NOT NULL DEFAULT 10000 CHECK (spoilage_bps BETWEEN 0 AND 10000),
  rto_level TEXT NOT NULL DEFAULT 'medium',
  rto_score INTEGER NOT NULL DEFAULT 0,
  rto_gate TEXT NOT NULL DEFAULT 'whatsapp_confirm',
  rto_reasons_json TEXT NOT NULL DEFAULT '[]',
  whatsapp_confirmed INTEGER NOT NULL DEFAULT 0 CHECK (whatsapp_confirmed IN (0, 1)),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_commerce_orders_store_created
  ON commerce_orders (store_id, created_at DESC, id DESC);

CREATE INDEX IF NOT EXISTS idx_commerce_orders_store_state
  ON commerce_orders (store_id, state, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_commerce_orders_phone_hash
  ON commerce_orders (customer_phone_hash, state);

CREATE TABLE IF NOT EXISTS commerce_order_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  order_ref TEXT NOT NULL,
  from_state TEXT,
  to_state TEXT NOT NULL,
  actor TEXT NOT NULL DEFAULT 'system',
  reason TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  FOREIGN KEY (order_ref) REFERENCES commerce_orders(order_ref)
);

CREATE INDEX IF NOT EXISTS idx_commerce_events_ref
  ON commerce_order_events (order_ref, id ASC);

CREATE TABLE IF NOT EXISTS commerce_rate_limits (
  bucket_key TEXT PRIMARY KEY,
  window_start INTEGER NOT NULL,
  hit_count INTEGER NOT NULL DEFAULT 1
);
