from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


EVENT_TYPES = frozenset({
    "candidate_detected",
    "snapshot_captured",
    "route_simulated",
    "gas_scenario_evaluated",
    "paper_order_simulated",
    "outcome_observed",
    "rejected",
    "expired",
})
ADDRESS_RE = re.compile(r"0x[0-9a-fA-F]{40}")
HASH_RE = re.compile(r"0x[0-9a-fA-F]{64}")
MAX_PAYLOAD_BYTES = 65_536
RAW_INTEGER_FIELDS = (
    "gross_profit_raw",
    "gas_used",
    "gas_price_wei",
    "gas_cost_profit_raw",
    "net_profit_raw",
)
METADATA_FIELDS = (
    "candidate_id",
    "event_id",
    "event_type",
    "occurred_at",
    "chain_id",
    "block_number",
    "block_hash",
    "strategy_id",
    "source_pool",
    "destination_pool",
    "token_in",
    "token_out",
    *RAW_INTEGER_FIELDS,
)


class PaperTradingLedger:
    """Append-only, hash-chained research events. This ledger never places orders."""

    def __init__(self, path: str | Path = "data/paper-trading.sqlite3"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS paper_events (
                    event_id TEXT PRIMARY KEY,
                    candidate_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL CHECK(sequence > 0),
                    event_type TEXT NOT NULL,
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
                    UNIQUE(candidate_id, sequence)
                )
            """)
            db.execute("""
                CREATE INDEX IF NOT EXISTS idx_paper_events_candidate_sequence
                ON paper_events(candidate_id, sequence)
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS paper_event_checkpoints (
                    candidate_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL CHECK(sequence > 0),
                    event_id TEXT NOT NULL UNIQUE,
                    event_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(candidate_id, sequence)
                )
            """)
            db.execute("""
                CREATE TRIGGER IF NOT EXISTS paper_event_checkpoints_no_update
                BEFORE UPDATE ON paper_event_checkpoints
                BEGIN
                    SELECT RAISE(ABORT, 'paper checkpoints are append-only');
                END
            """)
            db.execute("""
                CREATE TRIGGER IF NOT EXISTS paper_event_checkpoints_no_delete
                BEFORE DELETE ON paper_event_checkpoints
                BEGIN
                    SELECT RAISE(ABORT, 'paper checkpoints are append-only');
                END
            """)
            db.execute("""
                CREATE TRIGGER IF NOT EXISTS paper_events_no_update
                BEFORE UPDATE ON paper_events
                BEGIN
                    SELECT RAISE(ABORT, 'paper events are append-only');
                END
            """)
            db.execute("""
                CREATE TRIGGER IF NOT EXISTS paper_events_no_delete
                BEFORE DELETE ON paper_events
                BEGIN
                    SELECT RAISE(ABORT, 'paper events are append-only');
                END
            """)

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10)
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA busy_timeout=10000")
        return db

    @staticmethod
    def _text(value: str, name: str, maximum: int = 128) -> str:
        if not isinstance(value, str) or not value.strip() or len(value) > maximum:
            raise ValueError(f"{name} must contain 1 to {maximum} characters")
        return value

    @staticmethod
    def _address(value: str | None, name: str) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str) or not ADDRESS_RE.fullmatch(value):
            raise ValueError(f"{name} must be a 20-byte EVM address")
        if int(value[2:], 16) == 0:
            raise ValueError(f"{name} cannot be the zero address")
        return value.lower()

    @staticmethod
    def _integer(value: int | None, name: str, *, minimum: int | None = None) -> str | None:
        if value is None:
            return None
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError(f"{name} must be an integer")
        if minimum is not None and value < minimum:
            raise ValueError(f"{name} must be >= {minimum}")
        return str(value)

    @staticmethod
    def _hash_record(record: dict[str, Any], previous_hash: str) -> str:
        envelope = {"previous_hash": previous_hash, "record": record}
        canonical = json.dumps(
            envelope, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()

    def append_event(
        self,
        *,
        candidate_id: str,
        event_id: str,
        event_type: str,
        occurred_at: datetime,
        payload: dict[str, Any],
        chain_id: int | None = None,
        block_number: int | None = None,
        block_hash: str | None = None,
        strategy_id: str | None = None,
        source_pool: str | None = None,
        destination_pool: str | None = None,
        token_in: str | None = None,
        token_out: str | None = None,
        gross_profit_raw: int | None = None,
        gas_used: int | None = None,
        gas_price_wei: int | None = None,
        gas_cost_profit_raw: int | None = None,
        net_profit_raw: int | None = None,
    ) -> bool:
        """Append one event. Replaying the same event_id and content is an idempotent no-op."""
        candidate_id = self._text(candidate_id, "candidate_id")
        event_id = self._text(event_id, "event_id")
        if not isinstance(event_type, str) or event_type not in EVENT_TYPES:
            raise ValueError("Unsupported paper-trading event type")
        if not isinstance(occurred_at, datetime) or occurred_at.tzinfo is None or occurred_at.utcoffset() is None:
            raise ValueError("occurred_at must be timezone-aware")
        timestamp = occurred_at.astimezone(timezone.utc).isoformat()
        if not isinstance(payload, dict):
            raise ValueError("payload must be a JSON object")
        try:
            payload_json = json.dumps(
                payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                allow_nan=False,
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("payload must contain finite JSON-compatible values") from exc
        if len(payload_json.encode("utf-8")) > MAX_PAYLOAD_BYTES:
            raise ValueError("payload exceeds the 64 KiB safety limit")

        supplied_block_fields = (chain_id is not None, block_number is not None, block_hash is not None)
        if any(supplied_block_fields) and not all(supplied_block_fields):
            raise ValueError("chain_id, block_number, and block_hash must be supplied together")
        if chain_id is not None:
            if not isinstance(chain_id, int) or isinstance(chain_id, bool) or chain_id <= 0:
                raise ValueError("chain_id must be a positive integer")
            if not isinstance(block_number, int) or isinstance(block_number, bool) or block_number < 0:
                raise ValueError("block_number must be a non-negative integer")
            if not isinstance(block_hash, str) or not HASH_RE.fullmatch(block_hash):
                raise ValueError("block_hash must be a 32-byte hexadecimal hash")
            block_hash = block_hash.lower()
        if event_type in {
            "snapshot_captured", "route_simulated", "gas_scenario_evaluated",
            "paper_order_simulated", "outcome_observed",
        } and chain_id is None:
            raise ValueError(f"{event_type} requires canonical chain and block provenance")
        if strategy_id is not None:
            strategy_id = self._text(strategy_id, "strategy_id", 80)

        source_pool = self._address(source_pool, "source_pool")
        destination_pool = self._address(destination_pool, "destination_pool")
        token_in = self._address(token_in, "token_in")
        token_out = self._address(token_out, "token_out")
        if event_type == "route_simulated":
            if not all((strategy_id, source_pool, destination_pool, token_in, token_out)):
                raise ValueError("route_simulated requires strategy, both pools, and both token addresses")
            if source_pool == destination_pool:
                raise ValueError("route_simulated requires distinct source and destination pools")
            if raw_values["gross_profit_raw"] is None:
                raise ValueError("route_simulated requires gross_profit_raw")

        raw_values = {
            "gross_profit_raw": self._integer(gross_profit_raw, "gross_profit_raw"),
            "gas_used": self._integer(gas_used, "gas_used", minimum=1),
            "gas_price_wei": self._integer(gas_price_wei, "gas_price_wei", minimum=0),
            "gas_cost_profit_raw": self._integer(gas_cost_profit_raw, "gas_cost_profit_raw", minimum=0),
            "net_profit_raw": self._integer(net_profit_raw, "net_profit_raw"),
        }
        if event_type == "gas_scenario_evaluated" and any(
            raw_values[name] is None for name in ("gas_used", "gas_price_wei", "gas_cost_profit_raw", "net_profit_raw")
        ):
            raise ValueError("gas_scenario_evaluated requires gas and net-economics fields")

        record: dict[str, Any] = {
            "candidate_id": candidate_id,
            "event_id": event_id,
            "event_type": event_type,
            "occurred_at": timestamp,
            "chain_id": chain_id,
            "block_number": block_number,
            "block_hash": block_hash,
            "strategy_id": strategy_id,
            "source_pool": source_pool,
            "destination_pool": destination_pool,
            "token_in": token_in,
            "token_out": token_out,
            **raw_values,
            "payload_json": payload_json,
        }

        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                "SELECT * FROM paper_events WHERE event_id = ?", (event_id,),
            ).fetchone()
            if existing:
                columns = [item[1] for item in db.execute("PRAGMA table_info(paper_events)")]
                old = dict(zip(columns, existing, strict=True))
                if any(old.get(key) != value for key, value in record.items()):
                    raise ValueError("event_id already exists with different event content")
                return False

            previous = db.execute(
                "SELECT sequence, event_hash FROM paper_events "
                "WHERE candidate_id = ? ORDER BY sequence DESC LIMIT 1",
                (candidate_id,),
            ).fetchone()
            sequence = (int(previous[0]) + 1) if previous else 1
            previous_hash = str(previous[1]) if previous else "0" * 64
            event_hash = self._hash_record(
                {"sequence": sequence, **record}, previous_hash,
            )
            db.execute("""
                INSERT INTO paper_events (
                    event_id, candidate_id, sequence, event_type, occurred_at,
                    chain_id, block_number, block_hash, strategy_id,
                    source_pool, destination_pool, token_in, token_out,
                    gross_profit_raw, gas_used, gas_price_wei,
                    gas_cost_profit_raw, net_profit_raw, payload_json,
                    previous_hash, event_hash
                ) VALUES (
                    :event_id, :candidate_id, :sequence, :event_type, :occurred_at,
                    :chain_id, :block_number, :block_hash, :strategy_id,
                    :source_pool, :destination_pool, :token_in, :token_out,
                    :gross_profit_raw, :gas_used, :gas_price_wei,
                    :gas_cost_profit_raw, :net_profit_raw, :payload_json,
                    :previous_hash, :event_hash
                )
            """, {
                **record,
                "sequence": sequence,
                "previous_hash": previous_hash,
                "event_hash": event_hash,
            })
            db.execute(
                """INSERT INTO paper_event_checkpoints
                   (candidate_id, sequence, event_id, event_hash, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (candidate_id, sequence, event_id, event_hash, timestamp),
            )
        return True

    def events(self, candidate_id: str, *, limit: int = 500) -> list[dict[str, Any]]:
        candidate_id = self._text(candidate_id, "candidate_id")
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 5000:
            raise ValueError("limit must be between 1 and 5000")
        with self._connect() as db:
            db.row_factory = sqlite3.Row
            rows = db.execute(
                "SELECT * FROM paper_events WHERE candidate_id = ? ORDER BY sequence LIMIT ?",
                (candidate_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def verify_chain(self, candidate_id: str) -> bool:
        """Verify sequence continuity, previous hashes, and each event digest."""
        events = self.events(candidate_id, limit=5000)
        with self._connect() as db:
            checkpoints = db.execute(
                "SELECT sequence, event_id, event_hash FROM paper_event_checkpoints "
                "WHERE candidate_id = ? ORDER BY sequence",
                (candidate_id,),
            ).fetchall()
        if len(events) != len(checkpoints):
            return False
        previous_hash = "0" * 64
        for expected_sequence, (event, checkpoint) in enumerate(
            zip(events, checkpoints, strict=True), start=1,
        ):
            if (
                event["sequence"] != expected_sequence
                or event["previous_hash"] != previous_hash
                or checkpoint != (expected_sequence, event["event_id"], event["event_hash"])
            ):
                return False
            record = {
                key: event[key] for key in (*METADATA_FIELDS, "payload_json")
            }
            expected_hash = self._hash_record(
                {"sequence": expected_sequence, **record}, previous_hash,
            )
            if event["event_hash"] != expected_hash:
                return False
            previous_hash = expected_hash
        return True
