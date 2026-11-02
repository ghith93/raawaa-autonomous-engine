"""SQLite integration checks for the exact D1 monetary reservation/settlement SQL."""
from __future__ import annotations

import concurrent.futures
import re
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def worker_sql(pattern: str, label: str) -> str:
    source = (ROOT / "worker" / "usage.js").read_text(encoding="utf-8")
    match = re.search(pattern, source, re.DOTALL)
    if match is None:
        raise AssertionError(f"Could not locate {label} SQL in worker/usage.js")
    return match.group(1)


def reserve_sql() -> str:
    return worker_sql(
        r"const sql = [\x60](INSERT INTO model_usage_reservations.*?RETURNING id)[\x60];",
        "atomic monetary reservation",
    )


def settle_update_sql() -> str:
    return worker_sql(
        r'env\.DB\.prepare\(\s*"(UPDATE model_usage_reservations SET settled_at=.*?settled_at IS NULL)"\s*\)',
        "monetary reservation settlement update",
    )


def settle_insert_sql() -> str:
    return worker_sql(
        r"const insert = env\.DB\.prepare\(\s*[\x60](INSERT INTO model_usage\(.*?ON CONFLICT\(reservation_id\) DO NOTHING)[\x60]",
        "idempotent monetary usage insert",
    )


def stamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def reserve_params(now: datetime, *, estimate=50, daily=100, monthly=100, task=100):
    current = stamp(now)
    return [
        "groq", "openai/gpt-oss-20b", "research", "task-concurrent", current, estimate,
        now.strftime("%Y-%m-%d"), estimate, daily,
        now.strftime("%Y-%m"), estimate, monthly,
        "task-concurrent", "task-concurrent", estimate, task,
    ]


class D1UsageSqlIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db_path = Path(self.temp.name) / "usage.sqlite3"
        connection = sqlite3.connect(self.db_path)
        for migration in sorted((ROOT / "migrations").glob("*.sql")):
            connection.executescript(migration.read_text(encoding="utf-8"))
        connection.close()
        self.reserve = reserve_sql()
        self.update = settle_update_sql()
        self.insert = settle_insert_sql()

    def connect(self):
        return sqlite3.connect(self.db_path, timeout=15, isolation_level=None)

    def test_all_migrations_create_durable_usage_tables_and_indexes(self):
        connection = self.connect()
        versions = {row[0] for row in connection.execute("SELECT version FROM schema_migrations")}
        columns = {row[1] for row in connection.execute("PRAGMA table_info(model_usage)")}
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        indexes = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='index'")}
        connection.close()
        self.assertIn("0005_durable_monetary_usage_ledger", versions)
        self.assertTrue({"cost_nanos", "task_id", "reservation_id"} <= columns)
        self.assertIn("model_usage_reservations", tables)
        self.assertIn("idx_model_usage_reservation", indexes)

    def test_actual_worker_reservation_sql_is_atomic_under_concurrency(self):
        now = datetime.now(timezone.utc)
        params = reserve_params(now, estimate=50, daily=100, monthly=100, task=100)

        def reserve():
            connection = self.connect()
            try:
                row = connection.execute(self.reserve, params).fetchone()
                return int(row[0]) if row else None
            finally:
                connection.close()

        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
            outcomes = list(pool.map(lambda _: reserve(), range(12)))
        accepted = [item for item in outcomes if item is not None]
        self.assertEqual(len(accepted), 2)
        connection = self.connect()
        count, reserved = connection.execute(
            "SELECT COUNT(*),COALESCE(SUM(estimated_cost_nanos),0) FROM model_usage_reservations WHERE task_id=? AND settled_at IS NULL",
            ("task-concurrent",),
        ).fetchone()
        connection.close()
        self.assertEqual((count, reserved), (2, 100))

    def test_zero_cost_reservations_are_allowed_under_zero_budgets(self):
        now = datetime.now(timezone.utc)
        connection = self.connect()
        row = connection.execute(
            self.reserve,
            reserve_params(now, estimate=0, daily=0, monthly=0, task=0),
        ).fetchone()
        rejected = connection.execute(
            self.reserve,
            reserve_params(now, estimate=1, daily=0, monthly=0, task=0),
        ).fetchone()
        connection.close()
        self.assertIsNotNone(row)
        self.assertIsNone(rejected)

    def test_exact_worker_settlement_sql_records_cost_once(self):
        now = datetime.now(timezone.utc)
        connection = self.connect()
        reservation = connection.execute(
            self.reserve,
            reserve_params(now, estimate=0, daily=0, monthly=0, task=0),
        ).fetchone()
        reservation_id = int(reservation[0])
        settled_at = stamp(now)
        actual, input_tokens, output_tokens = 17, 120, 80
        connection.execute(
            self.update,
            (settled_at, actual, input_tokens, output_tokens, reservation_id),
        )
        insert_params = (
            input_tokens, output_tokens, actual / 1_000_000_000,
            settled_at, actual, reservation_id, settled_at,
            actual, input_tokens, output_tokens,
        )
        connection.execute(self.insert, insert_params)

        # A repeated identical settlement cannot duplicate the usage row.
        retry_time = stamp(datetime.now(timezone.utc))
        connection.execute(
            self.update,
            (retry_time, actual, input_tokens, output_tokens, reservation_id),
        )
        retry_params = (
            input_tokens, output_tokens, actual / 1_000_000_000,
            retry_time, actual, reservation_id, retry_time,
            actual, input_tokens, output_tokens,
        )
        connection.execute(self.insert, retry_params)
        rows = connection.execute(
            "SELECT COUNT(*),COALESCE(SUM(cost_nanos),0),MAX(task_id) FROM model_usage WHERE reservation_id=?",
            (reservation_id,),
        ).fetchone()
        reservation_row = connection.execute(
            "SELECT actual_cost_nanos,input_tokens,output_tokens FROM model_usage_reservations WHERE id=?",
            (reservation_id,),
        ).fetchone()
        connection.close()
        self.assertEqual(rows, (1, 17, "task-concurrent"))
        self.assertEqual(reservation_row, (17, 120, 80))


if __name__ == "__main__":
    unittest.main()
