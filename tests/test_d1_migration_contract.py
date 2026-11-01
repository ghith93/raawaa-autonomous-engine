"""Execute the production D1 migration with SQLite to catch real SQL/schema regressions."""
from pathlib import Path
import sqlite3
import unittest


ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = sorted((ROOT / "migrations").glob("*.sql"))


class D1MigrationContractTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.execute("PRAGMA foreign_keys = ON")
        self.sql = [path.read_text(encoding="utf-8") for path in MIGRATIONS]

    def tearDown(self):
        self.db.close()

    def test_migration_executes_and_is_idempotent(self):
        for migration in self.sql:
            self.db.executescript(migration)
        for migration in self.sql:
            self.db.executescript(migration)
        tables = {
            row[0] for row in self.db.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        self.assertTrue(
            {"schema_migrations", "tasks", "audit_log", "operations",
             "memories", "model_usage", "quota_usage", "quota_cooldowns",
             "quota_audit_events"}.issubset(tables)
        )
        versions = self.db.execute(
            "SELECT version FROM schema_migrations"
        ).fetchall()
        self.assertEqual(versions, [("0001_initial",), ("0002_quota_ledger",)])


    def test_quota_ledger_indexes_and_constraints(self):
        for migration in self.sql:
            self.db.executescript(migration)
        indexes = {row[1] for row in self.db.execute("PRAGMA index_list(quota_usage)")}
        self.assertIn("idx_quota_usage_route_time", indexes)
        self.assertIn("idx_quota_usage_task", indexes)
        self.db.execute(
            "INSERT INTO quota_usage(id,route_id,requests,tokens,occurred_at,task_id) VALUES(?,?,?,?,?,?)",
            ("reservation-1", "groq:account:model", 1, 10, "2026-11-01T00:00:00Z", "task-1")
        )
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute(
                "INSERT INTO quota_usage(id,route_id,requests,tokens,occurred_at,task_id) VALUES(?,?,?,?,?,?)",
                ("reservation-2", "groq:account:model", 0, 10, "2026-11-01T00:00:00Z", "task-1")
            )

    def test_task_idempotency_and_status_constraints(self):
        for migration in self.sql:
            self.db.executescript(migration)
        task = ("task-1", "idem-1", "research_brief", "Test", "{}",
                "pending", 0, 2, "2026-01-01T00:00:00Z",
                "2026-01-01T00:00:00Z")
        self.db.execute(
            """INSERT INTO tasks
            (id, idempotency_key, task_type, title, payload_json, status,
             attempts, max_attempts, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", task
        )
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute(
                """INSERT INTO tasks
                (id, idempotency_key, task_type, title, payload_json, status,
                 attempts, max_attempts, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                ("task-2", "idem-1", "research_brief", "Duplicate", "{}",
                 "pending", 0, 2, "2026-01-01T00:00:00Z",
                 "2026-01-01T00:00:00Z")
            )
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute(
                """INSERT INTO tasks
                (id, idempotency_key, task_type, title, payload_json, status,
                 attempts, max_attempts, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                ("task-3", "idem-3", "research_brief", "Invalid", "{}",
                 "unknown", 0, 2, "2026-01-01T00:00:00Z",
                 "2026-01-01T00:00:00Z")
            )

    def test_operations_and_usage_constraints(self):
        self.db.executescript(self.sql)
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute(
                """INSERT INTO operations
                (id, idempotency_key, operation_type, payload_json, status)
                VALUES ('op-1', 'op-key', 'publish', '{}', 'completed')"""
            )
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute(
                """INSERT INTO model_usage
                (provider, model, task_kind, input_tokens, output_tokens,
                 cost_usd, created_at)
                VALUES ('test', 'test-model', 'test', 0, 0, -0.01, 'now')"""
            )

    def test_audit_foreign_key_is_enforced(self):
        self.db.executescript(self.sql)
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute(
                """INSERT INTO audit_log
                (task_id, event, details_json, created_at)
                VALUES ('missing-task', 'claimed', '{}', 'now')"""
            )


if __name__ == "__main__":
    unittest.main()
