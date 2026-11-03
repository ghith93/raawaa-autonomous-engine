from pathlib import Path
import sqlite3
import unittest


ROOT = Path(__file__).resolve().parents[1]


class TaskInvocationMigrationTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.execute("PRAGMA foreign_keys=ON")
        for name in (
            "0001_initial.sql", "0002_research_candidates.sql",
            "0003_durable_quota_ledger.sql", "0004_paper_event_ledger.sql",
            "0005_telegram_webhook_receipts.sql", "0006_monetary_budget_ledger.sql",
            "0007_task_model_invocations.sql",
            "0008_workers_ai_neuron_ledger.sql",
        ):
            path = ROOT / "migrations" / name
            if path.exists():
                self.db.executescript(path.read_text(encoding="utf-8"))

    def tearDown(self):
        self.db.close()

    def _task(self):
        self.db.execute(
            """INSERT INTO tasks(id,idempotency_key,task_type,title,payload_json,status,
               attempts,max_attempts,created_at,updated_at,lease_token,lease_expires_at)
               VALUES('task-1','key-1','research','Research','{}','running',1,2,
               '2026-01-01','2026-01-01','lease-1','2999-01-01')"""
        )

    def test_migration_is_applied_and_invocation_identity_is_unique_per_task_step(self):
        self._task()
        self.db.execute(
            """INSERT INTO task_model_invocations
            (invocation_id,task_id,step_key,task_kind,request_id,status,created_at,updated_at)
            VALUES('inv-1','task-1','research:primary','research','req-1','prepared','now','now')"""
        )
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute(
                """INSERT INTO task_model_invocations
                (invocation_id,task_id,step_key,task_kind,request_id,status,created_at,updated_at)
                VALUES('inv-2','task-1','research:primary','research','req-2','prepared','now','now')"""
            )
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute(
                """INSERT INTO task_model_invocations
                (invocation_id,task_id,step_key,task_kind,request_id,status,created_at,updated_at)
                VALUES('inv-3','task-1','research:review','research','req-1','prepared','now','now')"""
            )

    def test_distinct_steps_have_distinct_ids_and_route_attempts_are_unique(self):
        self._task()
        for invocation, step, request_id in (
            ("inv-1", "research:primary", "req-1"),
            ("inv-2", "research:review", "req-2"),
        ):
            self.db.execute(
                """INSERT INTO task_model_invocations
                (invocation_id,task_id,step_key,task_kind,request_id,status,created_at,updated_at)
                VALUES(?, 'task-1', ?, 'research', ?, 'prepared', 'now', 'now')""",
                (invocation, step, request_id),
            )
        self.db.execute(
            """INSERT INTO task_model_invocation_attempts
            (invocation_id,route_id,status,created_at,updated_at)
            VALUES('inv-1','groq:model-a','rejected','now','now')"""
        )
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute(
                """INSERT INTO task_model_invocation_attempts
                (invocation_id,route_id,status,created_at,updated_at)
                VALUES('inv-1','groq:model-a','in_flight','now','now')"""
            )
        rows = self.db.execute(
            "SELECT invocation_id,request_id FROM task_model_invocations ORDER BY step_key"
        ).fetchall()
        self.assertEqual(rows, [("inv-1", "req-1"), ("inv-2", "req-2")])

    def test_invocation_lifecycle_constraint_rejects_unknown_state(self):
        self._task()
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute(
                """INSERT INTO task_model_invocations
                (invocation_id,task_id,step_key,task_kind,request_id,status,created_at,updated_at)
                VALUES('inv-1','task-1','step','research','req-1','retryable','now','now')"""
            )


if __name__ == "__main__":
    unittest.main()
