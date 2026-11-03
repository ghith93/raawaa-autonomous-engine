import tempfile
import unittest
import sqlite3
from pathlib import Path

from engine.task_store import TaskStore
from engine.worker import run_once


class TaskStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = TaskStore(Path(self.tmp.name) / "test.db")

    def tearDown(self):
        self.tmp.cleanup()

    def test_enqueue_is_idempotent(self):
        first = self.store.enqueue("research_brief", "Market scan", idempotency_key="same")
        second = self.store.enqueue("research_brief", "Market scan duplicate", idempotency_key="same")
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(len(self.store.list_tasks()), 1)

    def test_worker_processes_safe_task_as_dry_run(self):
        self.store.enqueue("research_brief", "Test task", {"topic": "agent frameworks"})
        result = run_once(self.store)
        self.assertEqual(result["status"], "succeeded")
        task = self.store.list_tasks()[0]
        self.assertEqual(task["status"], "succeeded")
        self.assertEqual(task["execution_mode"], "dry_run")
        self.assertIn("dry_run", task["result_json"])
        self.assertIn('"agent_id": "researcher"', task["result_json"])
        self.assertIn('"execution_started": false', task["result_json"])

    def test_worker_blocks_unknown_task_type(self):
        self.store.enqueue("shell", "Do arbitrary shell work")
        result = run_once(self.store)
        self.assertEqual(result["status"], "blocked")

    def test_claim_records_selected_execution_mode(self):
        task = self.store.enqueue("research_brief", "Mode tracking")
        claimed = self.store.claim_next(execution_mode="live_inference")
        self.assertEqual(claimed["id"], task["id"])
        self.assertEqual(claimed["execution_mode"], "live_inference")
        self.assertEqual(self.store.list_tasks()[0]["execution_mode"], "live_inference")
        with self.assertRaises(ValueError):
            self.store.claim_next(execution_mode="shell")

    def test_existing_database_rows_migrate_to_unknown_mode(self):
        legacy_path = Path(self.tmp.name) / "legacy.db"
        with sqlite3.connect(legacy_path) as db:
            db.execute("""CREATE TABLE tasks (
                id TEXT PRIMARY KEY, idempotency_key TEXT NOT NULL UNIQUE,
                task_type TEXT NOT NULL, title TEXT NOT NULL, payload_json TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('pending','running','succeeded','failed','blocked')),
                attempts INTEGER NOT NULL DEFAULT 0, max_attempts INTEGER NOT NULL DEFAULT 2,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL, result_json TEXT,
                error TEXT, lease_token TEXT, lease_expires_at TEXT
            )""")
            db.execute("""INSERT INTO tasks
                (id,idempotency_key,task_type,title,payload_json,status,attempts,max_attempts,created_at,updated_at)
                VALUES ('legacy','legacy-key','research_brief','Legacy','{}','succeeded',1,2,'2026-01-01','2026-01-01')""")
        migrated = TaskStore(legacy_path)
        self.assertEqual(migrated.list_tasks()[0]["execution_mode"], "unknown")

    def test_claim_empty_queue(self):
        self.assertIsNone(self.store.claim_next())

    def test_worker_retries_then_fails_after_attempt_budget(self):
        task = self.store.enqueue("research_brief", "Malformed payload", max_attempts=2)
        with sqlite3.connect(self.store.db_path) as db:
            db.execute("UPDATE tasks SET payload_json='not-json' WHERE id=?", (task["id"],))
        first = run_once(self.store)
        self.assertEqual(first["status"], "pending")
        second = run_once(self.store)
        self.assertEqual(second["status"], "failed")
        stored = self.store.list_tasks()[0]
        self.assertEqual(stored["attempts"], 2)
        self.assertEqual(stored["status"], "failed")
        self.assertEqual(stored["error"], "worker_error:AgentContractError")

    def test_recover_stale_running_task(self):
        task = self.store.enqueue("research_brief", "Interrupted", max_attempts=2)
        claimed = self.store.claim_next()
        self.assertEqual(claimed["id"], task["id"])
        with sqlite3.connect(self.store.db_path) as db:
            db.execute("UPDATE tasks SET lease_expires_at='2000-01-01T00:00:00+00:00' WHERE id=?",
                       (task["id"],))
        result = self.store.recover_stale(lease_timeout_seconds=60)
        self.assertEqual(result, {"requeued": 1, "failed": 0})
        self.assertEqual(self.store.list_tasks()[0]["status"], "pending")
        events = [item["event"] for item in self.store.audit_events()]
        self.assertIn("task_lease_recovered", events)

    def test_stale_task_at_attempt_limit_is_failed(self):
        task = self.store.enqueue("research_brief", "Exhausted", max_attempts=1)
        self.store.claim_next()
        with sqlite3.connect(self.store.db_path) as db:
            db.execute("UPDATE tasks SET lease_expires_at='2000-01-01T00:00:00+00:00' WHERE id=?",
                       (task["id"],))
        result = self.store.recover_stale(lease_timeout_seconds=60)
        self.assertEqual(result, {"requeued": 0, "failed": 1})
        self.assertEqual(self.store.list_tasks()[0]["status"], "failed")

    def test_old_worker_is_fenced_after_stale_lease_recovery(self):
        task = self.store.enqueue("research_brief", "Lease fencing", max_attempts=3)
        old_claim = self.store.claim_next(lease_timeout_seconds=1)
        with sqlite3.connect(self.store.db_path) as db:
            db.execute("UPDATE tasks SET lease_expires_at='2000-01-01T00:00:00+00:00' WHERE id=?",
                       (task["id"],))
        self.assertEqual(self.store.recover_stale(lease_timeout_seconds=1),
                         {"requeued": 1, "failed": 0})
        new_claim = self.store.claim_next()
        self.assertNotEqual(old_claim["lease_token"], new_claim["lease_token"])
        with self.assertRaisesRegex(ValueError, "lease ownership lost"):
            self.store.finish(task["id"], "succeeded", result={"stale": True},
                              lease_token=old_claim["lease_token"])
        self.store.finish(task["id"], "succeeded", result={"ok": True},
                          lease_token=new_claim["lease_token"])
        self.assertEqual(self.store.list_tasks()[0]["status"], "succeeded")

    def test_heartbeat_extends_only_current_unexpired_lease(self):
        task = self.store.enqueue("research_brief", "Heartbeat")
        claim = self.store.claim_next(lease_timeout_seconds=60)
        expiry = self.store.heartbeat(task["id"], claim["lease_token"], 120)
        self.assertGreater(expiry, claim["lease_expires_at"])
        with self.assertRaisesRegex(ValueError, "lease lost or expired"):
            self.store.heartbeat(task["id"], "not-the-token", 120)

    def test_audit_log_written(self):
        self.store.enqueue("system_audit", "Audit")
        events = self.store.audit_events()
        self.assertEqual(events[0]["event"], "task_enqueued")


if __name__ == "__main__":
    unittest.main()
