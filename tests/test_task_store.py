import tempfile
import unittest
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

    def test_audit_log_written(self):
        self.store.enqueue("system_audit", "Audit")
        events = self.store.audit_events()
        self.assertEqual(events[0]["event"], "task_enqueued")


if __name__ == "__main__":
    unittest.main()
