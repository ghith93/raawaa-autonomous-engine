"""Contract tests for the additive D1 schema migration.

SQLite's in-memory engine validates the SQL/schema shape without touching the
configured Cloudflare database or requiring network credentials.
"""
from pathlib import Path
import sqlite3
import unittest


ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = sorted((ROOT / "migrations").glob("*.sql"))"


class D1MigrationTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.sql = [path.read_text(encoding="utf-8") for path in MIGRATIONS]

    def tearDown(self):
        self.db.close()

    def test_migration_creates_expected_tables_and_indexes(self):
        for migration in self.sql:
            self.db.executescript(migration)
        tables = {
            row[0] for row in self.db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        self.assertTrue({
            "schema_migrations", "tasks", "audit_log", "operations",
            "memories", "model_usage", "quota_usage", "quota_cooldowns", "quota_audit_events",
        }.issubset(tables))

        indexes = {
            row[0] for row in self.db.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            )
        }
        self.assertTrue({
            "idx_tasks_status_created", "idx_audit_created",
            "idx_operations_status_created", "idx_memories_category_created",
            "idx_model_usage_created",
        }.issubset(indexes))

    def test_migration_is_idempotent(self):
        self.db.executescript(self.sql)
        self.db.executescript(self.sql)
        versions = self.db.execute(
            "SELECT version FROM schema_migrations"
        ).fetchall()
        self.assertEqual(versions, [("0001_initial",), ("0002_quota_ledger",)])

    def test_task_status_constraint_matches_runtime_contract(self):
        self.db.executescript(self.sql)
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute(
                """INSERT INTO tasks(
                    id,idempotency_key,task_type,title,payload_json,status,
                    created_at,updated_at
                ) VALUES('id','key','test','test','{}','unknown','now','now')"""
            )


if __name__ == "__main__":
    unittest.main()
