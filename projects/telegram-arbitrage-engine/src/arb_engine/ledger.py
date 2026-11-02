from __future__ import annotations

import json
import sqlite3
from statistics import median
from decimal import Decimal
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
            db.execute("""CREATE TABLE IF NOT EXISTS engine_state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )""")

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
                "retrieval_age_seconds": str(item.retrieval_age_seconds),
                "quote_timestamp_status": item.quote_timestamp_status,
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

    def summarize(self, limit: int = 500) -> list[dict[str, str | int]]:
        """Summarize repeated indicative observations; this is not a backtest or PnL report."""
        rows = self.recent(limit)
        grouped: dict[tuple[str, str, str, str, str], list[dict[str, str]]] = {}
        for row in rows:
            key = (
                row["chain"], row["base_symbol"], row["quote_symbol"],
                row["buy_venue"], row["sell_venue"],
            )
            grouped.setdefault(key, []).append(row)

        summaries: list[dict[str, str | int]] = []
        for key, observations in grouped.items():
            spreads = [Decimal(item["gross_spread_bps"]) for item in observations]
            timestamps = sorted(item["observed_at"] for item in observations)
            summaries.append({
                "chain": key[0],
                "pair": f"{key[1]}/{key[2]}",
                "buy_venue": key[3],
                "sell_venue": key[4],
                "observation_count": len(observations),
                "mean_gross_spread_bps": str(sum(spreads) / len(spreads)),
                "median_gross_spread_bps": str(median(spreads)),
                "max_gross_spread_bps": str(max(spreads)),
                "first_observed_at": timestamps[0],
                "last_observed_at": timestamps[-1],
                "interpretation": "repeated_indicative_observations_not_backtested_profit",
            })
        return sorted(
            summaries,
            key=lambda item: (int(item["observation_count"]), Decimal(str(item["mean_gross_spread_bps"]))),
            reverse=True,
        )

    def get_state(self, key: str) -> str | None:
        if not isinstance(key, str) or not key.strip() or len(key) > 100:
            raise ValueError("Invalid state key")
        with self._connect() as db:
            row = db.execute("SELECT value FROM engine_state WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def set_state(self, key: str, value: str) -> None:
        if not isinstance(key, str) or not key.strip() or len(key) > 100:
            raise ValueError("Invalid state key")
        if not isinstance(value, str) or len(value) > 200:
            raise ValueError("Invalid state value")
        with self._connect() as db:
            db.execute(
                """INSERT INTO engine_state(key, value, updated_at) VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at""",
                (key, value, datetime.now(timezone.utc).isoformat()),
            )
