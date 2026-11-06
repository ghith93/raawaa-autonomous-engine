"""Regression tests for the one-shot Groq smoke health preflight.

These tests never contact a Worker or a model provider.
"""
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from scripts.run_groq_free_inference_smoke import (
    _assert_only_exact_smoke_task_pending,
    _wait_for_authenticated_worker,
)


class _Response:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False


class _Store:
    def __init__(self):
        self.paths = []

    def _request(self, path):
        self.paths.append(path)
        return {"status": "ready"}

    def list_tasks(self, limit, worker_lane="general"):
        self.paths.append((limit, worker_lane))
        return []


class GroqSmokePreflightTests(unittest.TestCase):
    @patch.dict(os.environ, {
        "RAAWAA_ENGINE_API_URL": "https://raawaa-autonomous-engine-staging.example.workers.dev",
        "RAAWAA_ENGINE_WORKER_LANE": "specialist:researcher",
        "CF_ACCESS_CLIENT_ID": "",
        "CF_ACCESS_CLIENT_SECRET": "",
    })
    @patch("urllib.request.urlopen", return_value=_Response())
    def test_health_probe_waits_for_researcher_lane_capability_without_access_secrets(
        self, open_url
    ):
        store = _Store()
        result = _wait_for_authenticated_worker(store, timeout_seconds=1)

        request = open_url.call_args.args[0]
        self.assertEqual(
            request.get_header("User-agent"),
            "RaawaaAutonomousEngine/1.0 "
            "(+https://github.com/ghith93/raawaa-autonomous-engine)",
        )
        self.assertIsNone(request.get_header("Cf-access-client-id"))
        self.assertIsNone(request.get_header("Cf-access-client-secret"))
        self.assertEqual(result, {"status": "ready"})
        self.assertEqual(store.paths, [
            "/v1/mobile/status",
            (1, "specialist:researcher"),
        ])


class GroqSmokeLaneQueueTests(unittest.TestCase):
    class LaneStore:
        def __init__(self, rows):
            self.rows = rows
            self.calls = []

        def list_tasks(self, limit, worker_lane="general"):
            self.calls.append((limit, worker_lane))
            return [dict(row) for row in self.rows]

    def test_preflight_is_scoped_to_researcher_lane(self):
        store = self.LaneStore([{"id": "smoke-task", "status": "pending"}])
        _assert_only_exact_smoke_task_pending(store, "smoke-task")
        self.assertEqual(store.calls, [(100, "specialist:researcher")])

    def test_preflight_rejects_an_unrelated_pending_researcher_task(self):
        store = self.LaneStore([
            {"id": "smoke-task", "status": "pending"},
            {"id": "other-task", "status": "pending"},
        ])
        with self.assertRaisesRegex(SystemExit, "researcher lane contains work"):
            _assert_only_exact_smoke_task_pending(store, "smoke-task")

    def test_preflight_rejects_a_running_researcher_task(self):
        store = self.LaneStore([
            {"id": "smoke-task", "status": "pending"},
            {"id": "other-running", "status": "running"},
        ])
        with self.assertRaisesRegex(SystemExit, "researcher lane contains work"):
            _assert_only_exact_smoke_task_pending(store, "smoke-task")

    def test_preflight_fails_closed_on_incomplete_researcher_readback(self):
        store = self.LaneStore([
            {"id": f"task-{index}", "status": "succeeded"} for index in range(100)
        ])
        with self.assertRaisesRegex(SystemExit, "readback may be incomplete"):
            _assert_only_exact_smoke_task_pending(store, "smoke-task")



if __name__ == "__main__":
    unittest.main()
