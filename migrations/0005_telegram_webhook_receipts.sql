-- Mobile Telegram webhook receipts; additive and disabled until operator secrets are configured.
CREATE TABLE IF NOT EXISTS telegram_update_receipts (
  update_id INTEGER PRIMARY KEY,
  chat_id TEXT NOT NULL,
  command TEXT NOT NULL,
  processing_status TEXT NOT NULL CHECK(processing_status IN ('processing','completed','failed','unknown')),
  delivery_status TEXT NOT NULL CHECK(delivery_status IN ('pending','sent','failed','unknown')),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_telegram_receipts_chat_created
  ON telegram_update_receipts(chat_id, created_at);
INSERT OR IGNORE INTO schema_migrations(version) VALUES ('0005_telegram_webhook_receipts');
