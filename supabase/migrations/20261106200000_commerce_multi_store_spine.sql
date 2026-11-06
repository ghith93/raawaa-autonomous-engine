-- Isolated Multi-Store Commerce Spine (`commerce` schema) for Supabase PostgreSQL.
-- Completely locked down from PostgREST (`anon` / `authenticated`); accessible
-- exclusively through the `commerce-api` Edge Function.

CREATE SCHEMA IF NOT EXISTS commerce;
REVOKE ALL ON SCHEMA commerce FROM PUBLIC;
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
    EXECUTE 'REVOKE ALL ON SCHEMA commerce FROM anon';
  END IF;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
    EXECUTE 'REVOKE ALL ON SCHEMA commerce FROM authenticated';
  END IF;
END
$$;

CREATE TABLE IF NOT EXISTS commerce.orders (
  id BIGSERIAL PRIMARY KEY,
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
  lines_json JSONB NOT NULL,
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
  rto_reasons_json JSONB NOT NULL DEFAULT '[]'::jsonb,
  whatsapp_confirmed BOOLEAN NOT NULL DEFAULT false,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_commerce_orders_store_created
  ON commerce.orders (store_id, created_at DESC, id DESC);

CREATE INDEX IF NOT EXISTS idx_commerce_orders_store_state
  ON commerce.orders (store_id, state, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_commerce_orders_phone_hash
  ON commerce.orders (customer_phone_hash, state);

CREATE TABLE IF NOT EXISTS commerce.order_events (
  id BIGSERIAL PRIMARY KEY,
  order_ref TEXT NOT NULL REFERENCES commerce.orders(order_ref) ON DELETE CASCADE,
  from_state TEXT,
  to_state TEXT NOT NULL,
  actor TEXT NOT NULL DEFAULT 'system',
  reason TEXT NOT NULL DEFAULT '',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_commerce_events_ref
  ON commerce.order_events (order_ref, id ASC);

CREATE TABLE IF NOT EXISTS commerce.rate_limits (
  bucket_key TEXT PRIMARY KEY,
  window_start BIGINT NOT NULL,
  hit_count INTEGER NOT NULL DEFAULT 1
);

ALTER TABLE commerce.orders ENABLE ROW LEVEL SECURITY;
ALTER TABLE commerce.order_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE commerce.rate_limits ENABLE ROW LEVEL SECURITY;
