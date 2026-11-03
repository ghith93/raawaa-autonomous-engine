import sqlite3
import unittest
from pathlib import Path


class MonetaryLedgerMigrationTests(unittest.TestCase):
    def test_migration_backfills_existing_model_costs_into_durable_ledger(self):
        root = Path(__file__).resolve().parents[1]
        migration = (root / "migrations" / "0006_monetary_budget_ledger.sql").read_text(encoding="utf-8")
        db = sqlite3.connect(":memory:")
        db.execute("CREATE TABLE schema_migrations(version TEXT PRIMARY KEY)")
        db.execute("""CREATE TABLE model_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            provider TEXT NOT NULL,
            model TEXT NOT NULL,
            task_kind TEXT NOT NULL,
            input_tokens INTEGER NOT NULL,
            output_tokens INTEGER NOT NULL,
            cost_usd REAL NOT NULL CHECK(cost_usd >= 0),
            created_at TEXT NOT NULL
        )""")
        db.execute(
            "INSERT INTO model_usage(provider,model,task_kind,input_tokens,output_tokens,cost_usd,created_at) "
            "VALUES(?,?,?,?,?,?,?)",
            ("legacy-provider","legacy-model","research",12,8,0.125,"2026-11-03T01:02:03.000Z"),
        )
        db.executescript(migration)
        row = db.execute(
            "SELECT provider,model,task_id,status,estimated_cost_nanos,actual_cost_nanos,"
            "input_tokens,output_tokens,settled_at FROM monetary_reservations"
        ).fetchone()
        self.assertEqual(row, (
            "legacy-provider","legacy-model","legacy-unattributed","settled",
            125_000_000,125_000_000,12,8,"2026-11-03T01:02:03.000Z",
        ))
        self.assertEqual(
            db.execute("SELECT version FROM schema_migrations WHERE version='0006_monetary_budget_ledger'").fetchone(),
            ("0006_monetary_budget_ledger",),
        )
        self.assertEqual(
            db.execute("SELECT daily_budget_nanos,monthly_budget_nanos,per_task_budget_nanos,enabled "
                       "FROM monetary_budget_policy WHERE policy_key='default'").fetchone(),
            (400_000_000,12_000_000_000,200_000_000,1),
        )
        db.close()


if __name__ == "__main__":
    unittest.main()
