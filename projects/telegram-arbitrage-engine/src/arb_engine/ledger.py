from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .scanner import PriceSpread


class OpportunityLedger:
    """Append-only local research ledger; records observations, not orders."""

    def __init__(self, path: str | Path = "data/opportunities.sqlite3"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS observations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                observed_at TEXT NOT NULL,
                chain TEXT NOT NULL,
                base_symbol TEXT NOT NULL,
                quote_symbol TEXT NOT NULL,
                buy_venue TEXT NOT NULL,
                sell_venue TEXT NOT NULL,
                buy_price TEXT NOT NULL,
                sell_price TEXT NOT NULL,
                gross_spread_bps TEXT NOT NULL,
                min_liquidity_usd TEXT NOT NULL,
                total_volume_24h_usd TEXT NOT NULL,
                classification TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                UNIQUE(observed_at, chain, base_symbol, quote_symbol, buy_venue, sell_venue)
            )""")
            db.execute("CREATE INDEX IF NOT EXISTS idx_observations_pair_time ON observations(chain, base_symbol, observed_at)")

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10)
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA busy_timeout=10000")
        return db

    def record(self, spreads: list[PriceSpread]) -> int:
        observed_at = datetime.now(timezone.utc).isoformat()
        rows = []
        for item in spreads:
            payload = {
                "buy_pair_url": item.buy_pair_url,
                "sell_pair_url": item.sell_pair_url,
                "data_age_seconds": str(item.data_age_seconds),
            }
            rows.append((observed_at, item.chain, item.base_symbol, item.quote_symbol,
                         item.buy_venue, item.sell_venue, str(item.buy_price_usd),
                         str(item.sell_price_usd), str(item.gross_spread_bps),
                         str(item.min_pool_liquidity_usd), str(item.total_24h_volume_usd),
                         item.classification, json.dumps(payload, separators=(",", ":"))))
        if not rows:
            return 0
        with self._connect() as db:
            before = db.total_changes
            db.executemany("""INSERT OR IGNORE INTO observations
                (observed_at, chain, base_symbol, quote_symbol, buy_venue, sell_venue,
                 buy_price, sell_price, gross_spread_bps, min_liquidity_usd,
                 total_volume_24h_usd, classification, payload_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", rows)
            return db.total_changes - before

    def recent(self, limit: int = 20) -> list[dict[str, str]]:
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        with self._connect() as db:
            db.row_factory = sqlite3.Row
            rows = db.execute("SELECT * FROM observations ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(row) for row in rows]
