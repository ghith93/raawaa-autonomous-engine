"""End-to-end tests for a supervised task through the real task lifecycle.

These tests use the real `TaskStore` (real SQLite rows, real leases, real audit
events) and real child processes. Faults are injected only at trusted seam
arguments — a fake lease store, a pre-recorded dispatch row, a slow fixture
operation — never by mocking the process or the database.
"""
import json
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path

from engine.lease_heartbeat import LeaseHeartbeatLost
from engine.process_supervisor import (
    LeaseWatchdog,
    OperationRegistry,
    OperationSpec,
    ProcessSupervisor,
    SqliteDispatchLedger,
    default_registry,
)
from engine.supervised_task import (
    SUPERVISED_TASK_TYPE,
    SUPERVISOR_SWITCH,
    WORKER_OPERATIONS,
    complete_supervised_task,
    supervisor_enabled,
)
from engine.task_store import TaskStore
from engine.worker import SAFE_TASKS, run_once

PYTHON = sys.executable
ENABLED = {SUPERVISOR_SWITCH: "enabled"}
DISABLED = {SUPERVISOR_SWITCH: "disabled"}


def slow_registry(name: str = "sleep_fixture", wall_seconds: float = 20.0) -> OperationRegistry:
    return OperationRegistry([
        OperationSpec(
            name=name,
            executable=PYTHON,
            arguments=("-c", "import time;time.sleep(30)"),
            wall_seconds=wall_seconds,
            grace_seconds=0.3,
        )
    ])


class FlakyLeaseStore:
    """Wraps a real store whose heartbeat succeeds ``allowed`` times, then fails."""

    def __init__(self, inner, allowed: int):
        self.inner = inner
        self.allowed = allowed
        self.calls = 0

    def heartbeat(self, task_id, lease_token, lease_timeout_seconds=900):
        self.calls += 1
        if self.calls > self.allowed:
            raise ValueError("lease lost or expired")
        return self.inner.heartbeat(task_id, lease_token, lease_timeout_seconds)


class SupervisedTaskTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = Path(self._tmp.name) / "tasks.db"
        self.store = TaskStore(self.db_path)

    def enqueue(self, payload, key="supervised-1"):
        return self.store.enqueue(SUPERVISED_TASK_TYPE, "Supervised probe", payload,
                                  idempotency_key=key)

    def dispatch_rows(self):
        with sqlite3.connect(self.db_path) as db:
            db.row_factory = sqlite3.Row
            try:
                return [dict(row) for row in db.execute("SELECT * FROM process_dispatches")]
            except sqlite3.OperationalError:
                return []

    def audit_events(self):
        return [event["event"] for event in self.store.audit_events(50)]

    # -- the switch -------------------------------------------------------

    def test_switch_defaults_to_off_and_only_enabled_turns_it_on(self):
        self.assertFalse(supervisor_enabled({}))
        self.assertFalse(supervisor_enabled({"RAAWAA_ENGINE_PROCESS_SUPERVISOR": "yes"}))
        self.assertTrue(supervisor_enabled({"RAAWAA_ENGINE_PROCESS_SUPERVISOR": " Enabled "}))

    def test_disabled_switch_blocks_the_task_without_starting_anything(self):
        self.enqueue({"operation": "git_head_revision"})
        outcome = run_once(self.store, environ=DISABLED)
        self.assertEqual(outcome["status"], "blocked")
        self.assertEqual(outcome["reason"], "process_supervisor_disabled")
        task = self.store.list_tasks()[0]
        self.assertEqual(task["status"], "blocked")
        self.assertIn("disabled", task["error"])
        self.assertEqual(self.dispatch_rows(), [])

    # -- the happy path ---------------------------------------------------

    def test_a_real_task_runs_end_to_end_and_records_the_commit(self):
        task = self.enqueue({"operation": "git_head_revision"})
        outcome = run_once(self.store, environ=ENABLED)

        self.assertEqual(outcome["status"], "succeeded")
        self.assertEqual(outcome["classification"], "succeeded")
        recorded = self.store.list_tasks()[0]
        self.assertEqual(recorded["id"], task["id"])
        self.assertEqual(recorded["status"], "succeeded")
        self.assertEqual(recorded["attempts"], 1)
        # execution_mode keeps describing inference, not process execution.
        self.assertEqual(recorded["execution_mode"], "dry_run")
        self.assertIsNone(recorded["lease_token"])

        result = json.loads(recorded["result_json"])
        self.assertEqual(result["execution"], "supervised_process:v1")
        self.assertEqual(result["inference"], "none")
        self.assertEqual(result["provider_calls"], 0)
        self.assertFalse(result["replayed"])
        self.assertEqual(result["classification"], "succeeded")
        self.assertTrue(result["stdout"].strip())
        self.assertNotIn("environment", result)
        self.assertNotIn("HOME", result["stdout"])

        events = self.audit_events()
        for expected in ("task_enqueued", "task_claimed", "task_heartbeat", "task_succeeded"):
            self.assertIn(expected, events)

        rows = self.dispatch_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "settled")
        self.assertEqual(rows[0]["classification"], "succeeded")
        self.assertEqual(rows[0]["task_id"], task["id"])

    def test_the_worker_level_allowlist_is_narrower_than_the_registry(self):
        self.assertTrue(WORKER_OPERATIONS <= set(default_registry().names()))
        self.assertNotIn(SUPERVISED_TASK_TYPE, SAFE_TASKS)

    def test_an_operation_outside_the_worker_allowlist_is_blocked(self):
        # Present in the supervisor registry, but unreachable from a payload.
        self.assertIn("git_recent_subjects", default_registry().names())
        self.enqueue({"operation": "git_recent_subjects"})
        outcome = run_once(self.store, environ=ENABLED)
        self.assertEqual(outcome["status"], "blocked")
        self.assertEqual(outcome["reason"], "invalid_supervised_task")
        self.assertEqual(self.dispatch_rows(), [])

    def test_a_parameters_channel_is_refused(self):
        self.enqueue({"operation": "git_head_revision", "parameters": {"count": "1"}})
        outcome = run_once(self.store, environ=ENABLED)
        self.assertEqual(outcome["status"], "blocked")
        self.assertIn("exactly one key", outcome["detail"])
        self.assertEqual(self.dispatch_rows(), [])

    def test_an_executable_cannot_be_requested_from_a_payload(self):
        self.enqueue({"operation": "git_head_revision", "executable": "/bin/sh"})
        outcome = run_once(self.store, environ=ENABLED)
        self.assertEqual(outcome["status"], "blocked")
        self.assertEqual(self.dispatch_rows(), [])

    # -- failure, ambiguity, and the case that writes nothing --------------

    def test_a_timeout_fails_the_task_and_records_the_classification(self):
        self.enqueue({"operation": "sleep_fixture"})
        supervisor = ProcessSupervisor(slow_registry(wall_seconds=0.4),
                                       SqliteDispatchLedger(self.db_path),
                                       drain_grace_seconds=1.0)
        started = time.monotonic()
        outcome = complete_supervised_task(
            self.store, self.store.claim_next(execution_mode="dry_run"),
            environ=ENABLED, supervisor=supervisor,
            allowed_operations=frozenset({"sleep_fixture"}),
        )
        self.assertLess(time.monotonic() - started, 8.0)
        self.assertEqual(outcome["status"], "failed")
        self.assertEqual(outcome["classification"], "timed_out")
        recorded = self.store.list_tasks()[0]
        self.assertEqual(recorded["status"], "failed")
        result = json.loads(recorded["result_json"])
        self.assertEqual(result["classification"], "timed_out")
        self.assertIsNotNone(result["signal_number"])

    def test_an_interrupted_dispatch_blocks_the_task_instead_of_replaying_it(self):
        task = self.enqueue({"operation": "git_head_revision"})
        ledger = SqliteDispatchLedger(self.db_path)
        supervisor = ProcessSupervisor(default_registry(), ledger)
        dispatch_id = supervisor.dispatch_id(task["id"], "git_head_revision", {})
        # Exactly the row a supervisor that died mid-run leaves behind.
        ledger.begin(dispatch_id, task_id=task["id"], operation="git_head_revision",
                     argv_digest="0" * 32)

        outcome = run_once(self.store, environ=ENABLED)
        self.assertEqual(outcome["status"], "blocked")
        self.assertEqual(outcome["classification"], "ambiguous")
        self.assertIn("in_flight", outcome["reason"])
        recorded = self.store.list_tasks()[0]
        self.assertEqual(recorded["status"], "blocked")
        # The record exists but carries no captured output, because the process
        # never ran and a replay must not invent one.
        blocked_result = json.loads(recorded["result_json"])
        self.assertEqual(blocked_result["stdout"], "")
        self.assertFalse(blocked_result["replayed"] and blocked_result["stdout"])

        # Reconciliation quarantines the interrupted row, and the ledger still
        # refuses to hand that dispatch identity to anyone.
        self.assertEqual(ledger.reconcile(stale_after_seconds=0), 1)
        self.assertEqual(ledger.get(dispatch_id).status, "quarantined")
        replay = supervisor.run_operation("git_head_revision", {}, task_id=task["id"])
        self.assertEqual(replay.classification, "ambiguous")
        self.assertFalse(replay.started)
        self.assertIn("quarantined", replay.reason)
        self.assertEqual(len(self.dispatch_rows()), 1)

    def test_a_lost_lease_writes_nothing_and_leaves_the_task_to_recovery(self):
        self.enqueue({"operation": "sleep_fixture"})
        claimed = self.store.claim_next(execution_mode="dry_run")
        self.assertIsNotNone(claimed)

        # The first renewal (the fence before the spawn) succeeds; the next one
        # fails, which is exactly "the lease expired while the child was running".
        flaky = FlakyLeaseStore(self.store, allowed=1)
        watchdog = LeaseWatchdog(flaky, claimed["id"], claimed["lease_token"],
                                 renew_interval_seconds=0.05)
        supervisor = ProcessSupervisor(slow_registry(wall_seconds=20.0),
                                       SqliteDispatchLedger(self.db_path),
                                       drain_grace_seconds=1.0)

        started = time.monotonic()
        outcome = complete_supervised_task(
            self.store, claimed, environ=ENABLED, watchdog=watchdog,
            supervisor=supervisor, allowed_operations=frozenset({"sleep_fixture"}),
        )
        self.assertLess(time.monotonic() - started, 8.0)
        self.assertEqual(outcome["status"], "lease_lost")
        self.assertEqual(outcome["recovery"], "stale_lease")
        self.assertTrue(watchdog.lease_lost)

        row = self.store.list_tasks()[0]
        self.assertEqual(row["status"], "running")
        self.assertIsNone(row["result_json"])
        self.assertIn("task_claimed", self.audit_events())
        self.assertNotIn("task_succeeded", self.audit_events())

        # The existing recovery path owns the task from here. Nothing has
        # requeued it yet because the row's lease has not been declared expired,
        # so expire it the way a dead worker's lease really expires.
        self.assertEqual(self.store.recover_stale(), {"requeued": 0, "failed": 0})
        with sqlite3.connect(self.db_path) as db:
            db.execute("UPDATE tasks SET lease_expires_at=? WHERE id=?",
                       ("2000-01-01T00:00:00+00:00", claimed["id"]))
        self.assertEqual(self.store.recover_stale(), {"requeued": 1, "failed": 0})
        self.assertEqual(self.store.list_tasks()[0]["status"], "pending")

    def test_a_supervised_task_is_unreachable_without_its_switch(self):
        """The default deployment posture cannot start a process by accident."""
        self.enqueue({"operation": "git_head_revision"})
        outcome = run_once(self.store, environ={})
        self.assertEqual(outcome["status"], "blocked")
        self.assertEqual(outcome["reason"], "process_supervisor_disabled")
        self.assertEqual(self.dispatch_rows(), [])

    def test_a_rejected_envelope_is_blocked_not_failed(self):
        self.enqueue({"operation": 12345})
        outcome = run_once(self.store, environ=ENABLED)
        self.assertEqual(outcome["status"], "blocked")
        self.assertEqual(outcome["reason"], "invalid_supervised_task")
        self.assertEqual(self.store.list_tasks()[0]["status"], "blocked")


if __name__ == "__main__":
    unittest.main()
