"""Offline acceptance tests for the scheduled local task-cycle smoke."""
import os
import unittest
from unittest.mock import patch

from scripts.run_local_task_cycle import run_local_task_cycle


class LocalTaskCycleTests(unittest.TestCase):
    def test_local_fake_executor_claims_finishes_reopens_and_replays_idempotently(self):
        with patch.dict(os.environ, {}, clear=True):
            result = run_local_task_cycle("test-run-001")
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["mode"], "dry_run")
        self.assertEqual(result["tasks_processed"], 1)
        self.assertTrue(result["durable_reopen_verified"])
        self.assertTrue(result["idempotent_replay_verified"])
        self.assertEqual(result["audit_events"], ["task_claimed", "task_succeeded"])
        self.assertEqual(result["network_calls"], 0)
        self.assertEqual(result["provider_calls"], 0)
        self.assertEqual(result["remote_writes"], 0)

    def test_refuses_remote_or_live_environment(self):
        with patch.dict(os.environ, {"RAAWAA_ENGINE_API_URL": "https://example.invalid"}):
            with self.assertRaisesRegex(RuntimeError, "remote Engine API URL"):
                run_local_task_cycle("test-run-002")
        with patch.dict(os.environ, {"RAAWAA_ENGINE_LIVE_INFERENCE": "enabled"}):
            with self.assertRaisesRegex(RuntimeError, "live inference"):
                run_local_task_cycle("test-run-003")

    def test_rejects_unbounded_run_identifier(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ValueError):
                run_local_task_cycle("../unsafe")
