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
        self.assertIn("dry_run", task["result_json"])

    def test_worker_blocks_unknown_task_type(self):
        self.store.enqueue("shell", "Do arbitrary shell work")
        result = run_once(self.store)
        self.assertEqual(result["status"], "blocked")

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
        self.assertEqual(stored["error"], "worker_error:JSONDecodeError")

    def test_recover_stale_running_task(self):
        task = self.store.enqueue("research_brief", "Interrupted", max_attempts=2)
        claimed = self.store.claim_next()
        self.assertEqual(claimed["id"], task["id"])
        with sqlite3.connect(self.store.db_path) as db:
            db.execute("UPDATE tasks SET updated_at='2000-01-01T00:00:00+00:00' WHERE id=?",
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
            db.execute("UPDATE tasks SET updated_at='2000-01-01T00:00:00+00:00' WHERE id=?",
                       (task["id"],))
        result = self.store.recover_stale(lease_timeout_seconds=60)
        self.assertEqual(result, {"requeued": 0, "failed": 1})
        self.assertEqual(self.store.list_tasks()[0]["status"], "failed")

    def test_audit_log_written(self):
        self.store.enqueue("system_audit", "Audit")
        events = self.store.audit_events()
        self.assertEqual(events[0]["event"], "task_enqueued")


if __name__ == "__main__":
    unittest.main()
