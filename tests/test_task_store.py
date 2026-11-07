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
        second = self.store.enqueue("research_brief", "Market scan", idempotency_key="same")
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(len(self.store.list_tasks()), 1)

    def test_replay_rejects_changed_manifest_and_preserves_original(self):
        original = self.store.enqueue("research_brief", "Stable", {"x": 1, "nested": {"a": 1, "b": 2}},
                                      idempotency_key="manifest", max_attempts=3)
        replay = self.store.enqueue("research_brief", "Stable", {"nested": {"b": 2, "a": 1}, "x": 1},
                                    idempotency_key="manifest", max_attempts=3)
        self.assertEqual(original["id"], replay["id"])
        for task_type, title, payload, attempts in [
            ("content_draft", "Stable", {"x": 1, "nested": {"a": 1, "b": 2}}, 3),
            ("research_brief", "Changed", {"x": 1, "nested": {"a": 1, "b": 2}}, 3),
            ("research_brief", "Stable", {"x": 2, "nested": {"a": 1, "b": 2}}, 3),
            ("research_brief", "Stable", {"x": 1, "nested": {"a": 1, "b": 2}}, 2),
        ]:
            with self.subTest(task_type=task_type, title=title, payload=payload, attempts=attempts):
                with self.assertRaisesRegex(ValueError, "idempotency key conflicts"):
                    self.store.enqueue(task_type, title, payload, idempotency_key="manifest", max_attempts=attempts)
        self.assertEqual(len(self.store.list_tasks()), 1)
        self.assertEqual(self.store.list_tasks()[0]["id"], original["id"])

    def test_concurrent_replay_creates_only_one_task(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        barrier = Barrier(2)
        def enqueue():
            barrier.wait()
            return self.store.enqueue("research_brief", "Stable", {"x": 1}, idempotency_key="race")
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: enqueue(), range(2)))
        self.assertEqual(results[0]["id"], results[1]["id"])
        self.assertEqual(len(self.store.list_tasks()), 1)

    def test_concurrent_conflicting_manifests_have_one_winner(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        barrier = Barrier(2)
        def enqueue(title):
            barrier.wait()
            try:
                return self.store.enqueue("research_brief", title, idempotency_key="conflicting-race")["id"]
            except ValueError:
                return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(enqueue, ("First", "Second")))
        self.assertEqual(sum(result is not None for result in results), 1)
        self.assertEqual(len(self.store.list_tasks()), 1)

    def test_exact_claim_is_lane_scoped_and_does_not_sweep(self):
        general = self.store.enqueue("system_audit", "General")
        specialist = self.store.enqueue("research_brief", "Specialist", execution_lane="specialist:researcher")
        self.assertIsNone(self.store.claim_exact(specialist["id"]))
        self.assertIsNone(self.store.claim_exact("missing", worker_lane="specialist:researcher"))
        claim = self.store.claim_exact(specialist["id"], worker_lane="specialist:researcher")
        self.assertEqual(claim["id"], specialist["id"])
        self.assertIsNone(self.store.claim_exact(specialist["id"], worker_lane="specialist:researcher"))
        self.assertEqual(next(t for t in self.store.list_tasks() if t["id"] == general["id"])["status"], "pending")
        with self.assertRaises(ValueError):
            self.store.claim_exact(general["id"], execution_mode="live_inference")

    def test_exact_claim_respects_dependencies_and_probe_boundary(self):
        parent = self.store.enqueue("system_audit", "Parent")
        child = self.store.enqueue("research_brief", "Child", depends_on=[parent["id"]])
        probe = self.store.enqueue("supervised_probe", "Probe")
        self.assertIsNone(self.store.claim_exact(child["id"]))
        self.assertIsNone(self.store.claim_exact(probe["id"]))
        claim = self.store.claim_exact(parent["id"])
        self.store.finish(parent["id"], "succeeded", lease_token=claim["lease_token"], execution_mode="dry_run")
        self.assertEqual(self.store.claim_exact(child["id"])["id"], child["id"])

    def test_exact_and_generic_claim_race_has_one_winner(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        target = self.store.enqueue("research_brief", "Race")
        barrier = Barrier(2)
        def claim(exact):
            barrier.wait()
            return (self.store.claim_exact(target["id"]) if exact else
                    self.store.claim_next(execution_mode="dry_run"))
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(claim, (True, False)))
        self.assertEqual(sum(row is not None for row in results), 1)
        self.assertEqual(self.store.list_tasks()[0]["attempts"], 1)

    def test_generic_claim_never_exceeds_attempt_budget_or_blocks_other_work(self):
        exhausted = self.store.enqueue("research_brief", "Exhausted", priority=100, max_attempts=1)
        available = self.store.enqueue("research_brief", "Available", priority=10)
        with self.store._connect() as db:
            db.execute("UPDATE tasks SET attempts=max_attempts WHERE id=?", (exhausted["id"],))
        claim = self.store.claim_next(execution_mode="dry_run")
        self.assertEqual(claim["id"], available["id"])
        self.assertEqual(claim["attempts"], 1)
        self.assertIsNone(self.store.claim_exact(exhausted["id"]))
        self.assertIsNone(self.store.claim_next(execution_mode="dry_run"))
        row = next(t for t in self.store.list_tasks() if t["id"] == exhausted["id"])
        self.assertEqual((row["status"], row["attempts"]), ("pending", 1))
        self.assertFalse(any(event["task_id"] == exhausted["id"] and event["event"] == "task_claimed"
                             for event in self.store.audit_events()))

    def test_capped_task_remains_unclaimed_under_generic_exact_race(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        exhausted = self.store.enqueue("research_brief", "Exhausted", priority=100, max_attempts=1)
        available = self.store.enqueue("research_brief", "Available", priority=10)
        with self.store._connect() as db:
            db.execute("UPDATE tasks SET attempts=max_attempts WHERE id=?", (exhausted["id"],))
        barrier = Barrier(2)
        def race_claim(exact):
            barrier.wait()
            if exact:
                return self.store.claim_exact(exhausted["id"])
            return self.store.claim_next(execution_mode="dry_run")
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(race_claim, (True, False)))
        self.assertIsNone(results[0])
        self.assertEqual(results[1]["id"], available["id"])
        row = next(t for t in self.store.list_tasks() if t["id"] == exhausted["id"])
        self.assertEqual((row["status"], row["attempts"], row["lease_token"]), ("pending", 1, None))

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

    def test_finish_rejects_not_started_execution_mode(self):
        task = self.store.enqueue("research_brief", "Mode cannot revert after claim")
        claim = self.store.claim_next(execution_mode="dry_run")
        with self.assertRaisesRegex(ValueError, "invalid execution_mode"):
            self.store.finish(
                task["id"], "succeeded", result={"ok": True},
                lease_token=claim["lease_token"], execution_mode="not_started",
            )
        saved = self.store.list_tasks()[0]
        self.assertEqual(saved["status"], "running")
        self.assertEqual(saved["execution_mode"], "dry_run")

    # Queue priority is persisted; dependency eligibility is evaluated before this ordering.
    def test_priority_selects_highest_eligible_task_first(self):
        self.store.enqueue("research_brief", "Low", priority=10)
        high = self.store.enqueue("research_brief", "High", priority=90)
        claimed = self.store.claim_next(execution_mode="dry_run")
        self.assertEqual(claimed["id"], high["id"])
        self.assertEqual(claimed["priority"], 90)

    def test_dependencies_wait_for_success_in_the_same_execution_mode(self):
        parent = self.store.enqueue("research_brief", "Parent", priority=10)
        child = self.store.enqueue("content_draft", "Child", priority=100,
                                   depends_on=[parent["id"]])
        self.assertEqual(child["depends_on"], [parent["id"]])
        claimed_parent = self.store.claim_next(execution_mode="dry_run")
        self.assertEqual(claimed_parent["id"], parent["id"])
        self.store.finish(parent["id"], "succeeded", result={"mode": "dry_run"},
                          lease_token=claimed_parent["lease_token"])
        self.assertIsNone(self.store.claim_next(execution_mode="live_inference"))
        claimed_child = self.store.claim_next(execution_mode="dry_run")
        self.assertEqual(claimed_child["id"], child["id"])

    def test_failed_dependency_blocks_all_pending_descendants(self):
        parent = self.store.enqueue("research_brief", "Failing parent", max_attempts=1)
        child = self.store.enqueue("content_draft", "Child", depends_on=[parent["id"]])
        grandchild = self.store.enqueue("code_review", "Grandchild", depends_on=[child["id"]])
        claim = self.store.claim_next(execution_mode="dry_run")
        self.assertEqual(claim["id"], parent["id"])
        self.store.finish(parent["id"], "failed", error="test failure",
                          lease_token=claim["lease_token"])
        self.assertIsNone(self.store.claim_next(execution_mode="dry_run"))
        tasks = {task["id"]: task for task in self.store.list_tasks()}
        self.assertEqual(tasks[child["id"]]["status"], "blocked")
        self.assertEqual(tasks[grandchild["id"]]["status"], "blocked")
        self.assertTrue(any(event["event"] == "task_blocked_by_dependency"
                            for event in self.store.audit_events()))

    def test_enqueue_rejects_disabled_codespace_dependency_executor(self):
        parent = self.store.enqueue("codespace_test", "Disabled parent")
        with self.assertRaisesRegex(ValueError, "unsupported dependency executor"):
            self.store.enqueue("research_brief", "Dependent", depends_on=[parent["id"]])

    def test_enqueue_validates_dependencies_and_idempotency_manifest(self):
        with self.assertRaisesRegex(ValueError, "unknown task dependency"):
            self.store.enqueue("research_brief", "Unknown", depends_on=["missing-task"])
        with self.assertRaisesRegex(ValueError, "depends_on must be a unique list"):
            self.store.enqueue("research_brief", "Whitespace", depends_on=["   "])
        first = self.store.enqueue("research_brief", "Stable", idempotency_key="stable", priority=25)
        replay = self.store.enqueue("research_brief", "Stable", idempotency_key="stable", priority=25)
        self.assertEqual(first["id"], replay["id"])
        with self.assertRaisesRegex(ValueError, "idempotency key conflicts"):
            self.store.enqueue("research_brief", "Stable", idempotency_key="stable", priority=90)

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

    def test_concurrent_stale_recovery_counts_and_audits_once(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        task = self.store.enqueue("research_brief", "Stale recovery race")
        self.store.claim_next()
        with sqlite3.connect(self.store.db_path) as db:
            db.execute("UPDATE tasks SET lease_expires_at='2000-01-01T00:00:00+00:00' WHERE id=?", (task["id"],))
        barrier = Barrier(2)
        def recover():
            barrier.wait()
            return self.store.recover_stale(worker_lane="general")
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: recover(), range(2)))
        self.assertEqual(sum(result["requeued"] for result in results), 1)
        self.assertEqual(sum(result["failed"] for result in results), 0)
        events = [e for e in self.store.audit_events() if e["task_id"] == task["id"] and e["event"] == "task_lease_recovered"]
        self.assertEqual(len(events), 1)

    def test_stale_recovery_is_scoped_to_consumer_lane(self):
        general = self.store.enqueue("research_brief", "General stale task")
        specialist = self.store.enqueue(
            "research_brief", "Researcher stale task",
            execution_lane="specialist:researcher",
        )
        general_claim = self.store.claim_next(worker_lane="general")
        specialist_claim = self.store.claim_next(worker_lane="specialist:researcher")
        self.assertEqual(general_claim["id"], general["id"])
        self.assertEqual(specialist_claim["id"], specialist["id"])
        with sqlite3.connect(self.store.db_path) as db:
            db.execute("UPDATE tasks SET lease_expires_at='2000-01-01T00:00:00+00:00' WHERE id IN (?,?)",
                       (general["id"], specialist["id"]))
        self.assertEqual(self.store.recover_stale(worker_lane="general"),
                         {"requeued": 1, "failed": 0})
        tasks = {task["id"]: task for task in self.store.list_tasks()}
        self.assertEqual(tasks[general["id"]]["status"], "pending")
        self.assertEqual(tasks[specialist["id"]]["status"], "running")
        self.assertEqual(self.store.recover_stale(worker_lane="specialist:researcher"),
                         {"requeued": 1, "failed": 0})
        tasks = {task["id"]: task for task in self.store.list_tasks()}
        self.assertEqual(tasks[specialist["id"]]["status"], "pending")

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


    def test_generic_and_specialist_consumers_are_isolated(self):
        general = self.store.enqueue("research_brief", "General", idempotency_key="lane-general")
        specialist = self.store.enqueue(
            "research_brief", "Specialist", idempotency_key="lane-specialist",
            execution_lane="specialist:researcher",
        )
        claimed = self.store.claim_next(execution_mode="dry_run")
        self.assertEqual(claimed["id"], general["id"])
        self.assertEqual(claimed["execution_lane"], "general")
        self.store.finish(general["id"], "succeeded", result={"mode": "dry_run"},
                          lease_token=claimed["lease_token"])
        self.assertIsNone(self.store.claim_next(execution_mode="dry_run"))
        specialist_claim = self.store.claim_next(
            execution_mode="dry_run", worker_lane="specialist:researcher"
        )
        self.assertEqual(specialist_claim["id"], specialist["id"])
        self.assertEqual(specialist_claim["execution_lane"], "specialist:researcher")

    def test_lane_is_part_of_idempotency_identity(self):
        self.store.enqueue("research_brief", "Same key", idempotency_key="lane-replay",
                           execution_lane="general")
        with self.assertRaisesRegex(ValueError, "idempotency key conflicts"):
            self.store.enqueue("research_brief", "Same key", idempotency_key="lane-replay",
                               execution_lane="specialist:researcher")

    def test_wrong_lane_cannot_heartbeat_finish_or_retry(self):
        task = self.store.enqueue("research_brief", "Owned", execution_lane="specialist:researcher")
        claim = self.store.claim_next(execution_mode="dry_run", worker_lane="specialist:researcher")
        with self.assertRaisesRegex(ValueError, "lease lost or expired"):
            self.store.heartbeat(task["id"], claim["lease_token"], 60, worker_lane="general")
        with self.assertRaisesRegex(ValueError, "worker lane ownership lost"):
            self.store.finish(task["id"], "succeeded", result={"mode": "dry_run"},
                              lease_token=claim["lease_token"], worker_lane="general")
        with self.assertRaisesRegex(ValueError, "worker lane ownership lost"):
            self.store.retry_or_fail(task["id"], "wrong owner", claim["lease_token"],
                                     worker_lane="general")
        self.store.finish(task["id"], "succeeded", result={"mode": "dry_run"},
                          lease_token=claim["lease_token"], worker_lane="specialist:researcher")

    def test_failed_specialist_dependency_does_not_block_general_claim_path(self):
        parent = self.store.enqueue("research_brief", "Parent", max_attempts=1,
                                    execution_lane="specialist:researcher")
        child = self.store.enqueue("research_brief", "Child",
                                   execution_lane="specialist:researcher", depends_on=[parent["id"]])
        parent_claim = self.store.claim_next(execution_mode="dry_run",
                                             worker_lane="specialist:researcher")
        self.store.finish(parent["id"], "failed", error="expected",
                          lease_token=parent_claim["lease_token"],
                          worker_lane="specialist:researcher")
        self.assertIsNone(self.store.claim_next(execution_mode="dry_run", worker_lane="general"))
        stored = {task["id"]: task for task in self.store.list_tasks()}
        self.assertEqual(stored[child["id"]]["status"], "pending")
        self.assertIsNone(self.store.claim_next(execution_mode="dry_run",
                                                worker_lane="specialist:researcher"))
        self.assertEqual({task["id"]: task for task in self.store.list_tasks()}[child["id"]]["status"],
                         "blocked")

    def test_stale_recovery_is_scoped_to_the_requesting_lane(self):
        general = self.store.enqueue("research_brief", "General stale")
        specialist = self.store.enqueue("research_brief", "Specialist stale",
                                        execution_lane="specialist:researcher")
        general_claim = self.store.claim_next(worker_lane="general")
        specialist_claim = self.store.claim_next(worker_lane="specialist:researcher")
        with sqlite3.connect(self.store.db_path) as db:
            db.execute("UPDATE tasks SET lease_expires_at='2000-01-01T00:00:00+00:00' WHERE id IN (?,?)",
                       (general["id"], specialist["id"]))
        self.assertEqual(self.store.recover_stale(worker_lane="general"),
                         {"requeued": 1, "failed": 0})
        saved = {task["id"]: task for task in self.store.list_tasks()}
        self.assertEqual(saved[general["id"]]["status"], "pending")
        self.assertEqual(saved[specialist["id"]]["status"], "running")
        self.assertEqual(self.store.recover_stale(worker_lane="specialist:researcher"),
                         {"requeued": 1, "failed": 0})

    def test_task_listing_can_be_scoped_to_one_lane(self):
        general = self.store.enqueue("research_brief", "General visible")
        specialist = self.store.enqueue("research_brief", "Researcher visible",
                                        execution_lane="specialist:researcher")
        general_rows = self.store.list_tasks(50, worker_lane="general")
        specialist_rows = self.store.list_tasks(50, worker_lane="specialist:researcher")
        self.assertEqual([task["id"] for task in general_rows], [general["id"]])
        self.assertEqual([task["id"] for task in specialist_rows], [specialist["id"]])

    def test_specialist_lane_must_match_task_contract(self):
        with self.assertRaisesRegex(ValueError, "does not match task_type"):
            self.store.enqueue("content_draft", "Wrong lane",
                               execution_lane="specialist:researcher")


if __name__ == "__main__":
    unittest.main()
